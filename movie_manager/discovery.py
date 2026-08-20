import logging
import os
import threading
import time
from typing import Dict, List

import requests

from .config import NETWORK_RETRY_SECONDS
from .db import (
    count_movies, create_job, find_probable_title_duplicate, get_search_state,
    get_latest_job, get_latest_source_query, movie_exists, save_search_state,
    update_job, upsert_movie, upsert_movie_with_target_guard,
)
from .events import events
from .language_profiles import (
    BLOCKED_TERMS, COMPILATION_DESCRIPTION_PHRASES, COMPILATION_TITLE_PHRASES, PROFILES,
)
from .utils import parse_iso8601_duration, normalise_title

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
ACCEPTED_STATUSES = ["ACCEPTED", "QUEUED", "DOWNLOADING", "DOWNLOADED"]
TEMPORARY_API_STATUSES = {429, 500, 502, 503, 504}
logger = logging.getLogger(__name__)


class YouTubeAPIError(RuntimeError):
    def __init__(self, status_code, reason, message):
        self.status_code = status_code
        self.reason = reason
        self.api_message = message
        label = f" ({reason})" if reason else ""
        super().__init__(f"YouTube API request rejected with HTTP {status_code}{label}: {message}")


def _safe_params(params):
    return {
        name: "<redacted>" if name.lower() == "key" else value
        for name, value in params.items()
    }


def _api_error(response):
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}
    errors = error.get("errors") or []
    reason = errors[0].get("reason", "") if errors else ""
    message = error.get("message") or "The request was rejected by YouTube."
    return reason, message


class DiscoveryController:
    def __init__(self):
        self._thread = None
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.status = "IDLE"
        self.language = "yoruba"
        self.target = 30
        self.stats = self._fresh_stats()
        self.current_query = ""
        self.message = ""
        self.job_id = None
        self.network_wait = False

    def _fresh_stats(self):
        return {
            "candidates_scanned": 0,
            "accepted": 0,
            "rejected_under_duration": 0,
            "rejected_not_movie": 0,
            "rejected_wrong_language": 0,
            "duplicates_skipped": 0,
            "api_requests": 0,
            "network_retries": 0,
        }

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "language": self.language,
                "target": self.target,
                "current_query": self.current_query,
                "message": self.message,
                "stats": dict(self.stats),
                "network_wait": self.network_wait,
            }

    def _job_stats(self):
        stats = dict(self.stats)
        stats["current_query"] = self.current_query
        return stats

    def restore_latest(self, language):
        """Restore the last discovery run when this process has no active worker."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.snapshot()

            job = get_latest_job("DISCOVERY", language, require_activity=True)
            if not job:
                return self.snapshot()

            restored = self._fresh_stats()
            for key in restored:
                restored[key] = int(job["stats"].get(key, 0) or 0)
            self.stats = restored
            self.status = job["status"]
            self.language = job["language"]
            self.target = int(job["target"] or self.target)
            self.current_query = (
                job["stats"].get("current_query") or get_latest_source_query(language)
            )
            self.message = job.get("message") or "Discovery completed."
            self.job_id = None
            self.network_wait = False
            return self.snapshot()

    def start(self, language: str, target: int):
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self.status == "PAUSED":
                    self.target = target
                    self._pause.clear()
                    self.status = "RUNNING"
                    events.emit("discovery_status", self.snapshot())
                    return
                raise RuntimeError("Discovery is already running.")
            profile = PROFILES.get(language)
            if not profile or not profile.get("enabled"):
                raise ValueError(f"Language '{language}' is not enabled yet.")
            self.language = language
            self.target = max(1, int(target))
            self._pause.clear()
            self._stop.clear()
            self.stats = self._fresh_stats()
            self.network_wait = False
            accepted_now = count_movies(language, ACCEPTED_STATUSES)
            if accepted_now >= self.target:
                self.restore_latest(language)
                self.target = max(1, int(target))
                self.language = language
                self.status = "COMPLETED"
                if not self.message:
                    self.message = f"Target already satisfied: {accepted_now}/{self.target}"
                events.emit("discovery_status", self.snapshot())
                return
            self.status = "RUNNING"
            self.message = "Starting discovery..."
            self.current_query = ""
            self.job_id = create_job(
                "DISCOVERY", language, self.target, "RUNNING", self._job_stats(), self.message
            )
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            events.emit("discovery_status", self.snapshot())

    def pause(self):
        with self._lock:
            if self.status == "RUNNING":
                self._pause.set()
                self.status = "PAUSED"
                self.message = "Paused by user."
                if self.job_id:
                    update_job(self.job_id, status="PAUSED", stats=self._job_stats(), message=self.message)
                events.emit("discovery_status", self.snapshot())

    def resume(self):
        with self._lock:
            if self.status == "PAUSED":
                self._pause.clear()
                self.status = "RUNNING"
                self.message = "Resumed."
                if self.job_id:
                    update_job(self.job_id, status="RUNNING", stats=self._job_stats(), message=self.message)
                events.emit("discovery_status", self.snapshot())

    def stop(self):
        self._stop.set()
        self._pause.clear()
        with self._lock:
            if self.status not in {"IDLE", "COMPLETED"}:
                self.status = "STOPPING"
                self.message = "Stopping safely..."
                self.network_wait = False
                events.emit("discovery_status", self.snapshot())

    def _wait_if_paused(self):
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.25)

    def _request(self, url, params):
        attempt = 0
        while not self._stop.is_set():
            self._wait_if_paused()
            try:
                response = requests.get(url, params=params, timeout=35)
                with self._lock:
                    self.stats["api_requests"] += 1
                    recovered = self.network_wait
                    self.network_wait = False
                if recovered:
                    events.emit("discovery_status", self.snapshot())
                if response.status_code == 200:
                    return response
                if response.status_code in TEMPORARY_API_STATUSES:
                    raise requests.RequestException(f"Temporary YouTube/API error {response.status_code}")
                reason, message = _api_error(response)
                logger.error(
                    "YouTube API permanent error: status=%s reason=%s message=%s params=%s",
                    response.status_code, reason or "unknown", message, _safe_params(params),
                )
                raise YouTubeAPIError(response.status_code, reason, message)
            except requests.RequestException as exc:
                delay = NETWORK_RETRY_SECONDS[min(attempt, len(NETWORK_RETRY_SECONDS) - 1)]
                attempt += 1
                with self._lock:
                    self.stats["network_retries"] += 1
                    self.message = f"Network/API unavailable. Retrying automatically in {delay}s..."
                    self.network_wait = True
                events.emit("network_wait", {"scope": "discovery", "delay": delay, "error": str(exc)})
                events.emit("discovery_status", self.snapshot())
                for _ in range(delay * 4):
                    if self._stop.is_set():
                        return None
                    self._wait_if_paused()
                    time.sleep(0.25)
        return None

    def _search(self, params):
        try:
            return self._request(SEARCH_URL, params)
        except YouTubeAPIError as exc:
            if exc.status_code != 400 or "relevanceLanguage" not in params:
                raise

            compatible_params = dict(params)
            unsupported_value = compatible_params.pop("relevanceLanguage")
            logger.warning(
                "YouTube rejected optional relevanceLanguage=%s; retrying with params=%s",
                unsupported_value, _safe_params(compatible_params),
            )
            with self._lock:
                self.message = "YouTube rejected an optional language hint; retrying compatibly."
            events.emit("discovery_status", self.snapshot())
            return self._request(SEARCH_URL, compatible_params)

    def _fetch_details(self, ids: List[str]) -> List[Dict]:
        if not ids:
            return []
        key = os.getenv("YOUTUBE_API_KEY", "").strip()
        params = {
            "part": "snippet,contentDetails,status",
            "id": ",".join(ids),
            "maxResults": 50,
            "key": key,
        }
        r = self._request(VIDEOS_URL, params)
        return [] if r is None else r.json().get("items", [])

    @staticmethod
    def _is_yoruba_language(value):
        value = (value or "").strip().lower().replace("_", "-")
        return value in {"yo", "yor", "yoruba"} or value.startswith("yo-")

    @staticmethod
    def _is_english_language(value):
        value = (value or "").strip().lower().replace("_", "-")
        return value in {"en", "eng", "english"} or value.startswith("en-")

    @staticmethod
    def _has_yoruba_content_evidence(title, description, channel_title):
        title = (title or "").lower()
        description = (description or "").lower()
        channel_title = (channel_title or "").lower()
        return (
            "yoruba" in title
            or "yorùbá" in title
            or any(phrase in description for phrase in (
                "yoruba movie", "yoruba film", "yoruba language", "yoruba nollywood",
            ))
            or "yoruba" in channel_title
            or "yorùbá" in channel_title
        )

    def _is_yoruba_candidate(self, snippet, title, description, query):
        audio_language = snippet.get("defaultAudioLanguage") or ""
        default_language = snippet.get("defaultLanguage") or ""
        has_yoruba_metadata = any(
            self._is_yoruba_language(value) for value in (audio_language, default_language)
        )
        has_english_metadata = any(
            self._is_english_language(value) for value in (audio_language, default_language)
        )
        has_content_evidence = self._has_yoruba_content_evidence(
            title, description, snippet.get("channelTitle")
        )
        query_is_yoruba_specific = "yoruba" in (query or "").lower()

        if has_yoruba_metadata:
            return True
        if has_english_metadata and not has_content_evidence:
            return False
        return query_is_yoruba_specific and has_content_evidence

    def _record_result(self, result):
        with self._lock:
            if result == "ACCEPTED":
                self.stats["accepted"] = count_movies(self.language, ACCEPTED_STATUSES)
            elif result == "UNDER_DURATION":
                self.stats["rejected_under_duration"] += 1
            elif result == "WRONG_LANGUAGE":
                self.stats["rejected_wrong_language"] += 1
            elif result == "NOT_MOVIE":
                self.stats["rejected_not_movie"] += 1
            elif result == "DUPLICATE":
                self.stats["duplicates_skipped"] += 1
            elif result == "TARGET_REACHED":
                self.stats["accepted"] = count_movies(self.language, ACCEPTED_STATUSES)

    def _evaluate(self, item, query, min_seconds):
        vid = item["id"]
        snippet = item.get("snippet", {})
        details = item.get("contentDetails", {})
        status = item.get("status", {})
        title = (snippet.get("title") or "").strip()
        description = (snippet.get("description") or "").strip()
        duration = parse_iso8601_duration(details.get("duration"))
        lowered = f"{title} {description}".lower()

        movie = {
            "language": self.language,
            "video_id": vid,
            "title": title or vid,
            "normalised_title": normalise_title(title),
            "description": description[:5000],
            "channel_id": snippet.get("channelId"),
            "channel_title": snippet.get("channelTitle"),
            "published_at": snippet.get("publishedAt"),
            "default_audio_language": snippet.get("defaultAudioLanguage"),
            "default_language": snippet.get("defaultLanguage"),
            "duration_seconds": duration,
            "thumbnail_url": (
                snippet.get("thumbnails", {}).get("maxres", {}).get("url")
                or snippet.get("thumbnails", {}).get("high", {}).get("url")
                or snippet.get("thumbnails", {}).get("medium", {}).get("url")
                or snippet.get("thumbnails", {}).get("default", {}).get("url")
            ),
            "youtube_url": f"https://www.youtube.com/watch?v={vid}",
            "embeddable": bool(status.get("embeddable", False)),
            "licence": status.get("license"),
            "status": "DISCOVERED",
            "download_status": "NOT_READY",
            "source_query": query,
        }

        if duration < min_seconds:
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = "UNDER_60_MINUTES"
            return movie, "UNDER_DURATION"

        if self.language == "yoruba" and not self._is_yoruba_candidate(snippet, title, description, query):
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = "WRONG_LANGUAGE"
            return movie, "WRONG_LANGUAGE"

        title_lowered = title.lower()
        title_phrase = next((phrase for phrase in COMPILATION_TITLE_PHRASES if phrase in title_lowered), None)
        description_phrase = next(
            (phrase for phrase in COMPILATION_DESCRIPTION_PHRASES if phrase in description.lower()), None
        )
        if title_phrase or description_phrase:
            movie["status"] = "REJECTED"
            scope = "TITLE" if title_phrase else "DESCRIPTION"
            movie["rejection_reason"] = f"COMPILATION_{scope}:{title_phrase or description_phrase}"
            return movie, "NOT_MOVIE"

        blocked = next((term for term in BLOCKED_TERMS if term in lowered), None)
        if blocked:
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = f"BLOCKED_TERM:{blocked}"
            return movie, "NOT_MOVIE"

        duplicate = find_probable_title_duplicate(
            self.language, movie["normalised_title"], duration, exclude_video_id=vid
        )
        if duplicate:
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = f"PROBABLE_DUPLICATE:{duplicate['video_id']}"
            return movie, "DUPLICATE"

        movie["status"] = "ACCEPTED"
        return movie, "ACCEPTED"

    def _run(self):
        try:
            key = os.getenv("YOUTUBE_API_KEY", "").strip()
            if not key or key == "PASTE_YOUR_PRIVATE_KEY_HERE":
                raise RuntimeError("YouTube API key is missing. Add it to the .env file.")

            profile = PROFILES[self.language]
            min_seconds = 60 * 60

            while not self._stop.is_set():
                accepted_now = count_movies(self.language, ACCEPTED_STATUSES)
                with self._lock:
                    self.stats["accepted"] = accepted_now
                if accepted_now >= self.target:
                    with self._lock:
                        self.status = "COMPLETED"
                        self.message = f"Target reached: {accepted_now}/{self.target}"
                    break

                progressed = False
                for query in profile["queries"]:
                    if self._stop.is_set():
                        break
                    self._wait_if_paused()
                    accepted_now = count_movies(self.language, ACCEPTED_STATUSES)
                    if accepted_now >= self.target:
                        break

                    token, exhausted = get_search_state(self.language, query)
                    if exhausted:
                        continue

                    with self._lock:
                        self.current_query = query
                        self.message = f"Searching: {query}"
                    events.emit("discovery_status", self.snapshot())

                    params = {
                        "part": "snippet",
                        "type": "video",
                        "maxResults": 50,
                        "q": query,
                        "regionCode": profile["region_code"],
                        "videoDuration": "long",
                        "safeSearch": "moderate",
                        "key": key,
                    }
                    if profile.get("relevance_language"):
                        params["relevanceLanguage"] = profile["relevance_language"]
                    if token:
                        params["pageToken"] = token

                    response = self._search(params)
                    if response is None:
                        break
                    payload = response.json()
                    progressed = True
                    items = payload.get("items", [])
                    ids = [
                        i.get("id", {}).get("videoId")
                        for i in items
                        if i.get("id", {}).get("videoId")
                    ]

                    new_ids = [vid for vid in ids if not movie_exists(self.language, vid)]
                    existing_count = len(ids) - len(new_ids)
                    with self._lock:
                        self.stats["duplicates_skipped"] += existing_count
                        self.stats["candidates_scanned"] += len(ids)

                    details = self._fetch_details(new_ids)
                    for item in details:
                        if self._stop.is_set():
                            break
                        self._wait_if_paused()
                        movie, result = self._evaluate(item, query, min_seconds)

                        if result == "ACCEPTED":
                            # Atomically re-checks accepted count against target at write
                            # time, so a concurrent writer (another thread or process)
                            # can never push the accepted total past target.
                            stored_status = upsert_movie_with_target_guard(
                                movie, ACCEPTED_STATUSES, self.target
                            )
                            if stored_status != "ACCEPTED":
                                result = "TARGET_REACHED"
                            movie = {**movie, "status": stored_status}
                        else:
                            upsert_movie(movie)
                        progressed = True

                        self._record_result(result)

                        events.emit("movie_processed", {
                            "result": result,
                            "movie": movie,
                            "stats": dict(self.stats),
                            "target": self.target,
                        })

                        if count_movies(self.language, ACCEPTED_STATUSES) >= self.target:
                            break

                    next_token = payload.get("nextPageToken")
                    save_search_state(self.language, query, next_token, not bool(next_token))
                    if self.job_id:
                        update_job(self.job_id, stats=self._job_stats(), message=self.message)

                if count_movies(self.language, ACCEPTED_STATUSES) >= self.target:
                    continue

                if not progressed:
                    with self._lock:
                        self.status = "COMPLETED"
                        self.message = (
                            "Search sources are exhausted before the target was reached. "
                            "Reset search state or add more search terms later."
                        )
                    break

            if self._stop.is_set():
                with self._lock:
                    self.status = "STOPPED"
                    self.message = "Stopped safely. Existing progress is saved."

        except Exception as exc:
            with self._lock:
                self.status = "ERROR"
                self.message = str(exc)
            events.emit("error", {"scope": "discovery", "message": str(exc)})
        finally:
            with self._lock:
                self.network_wait = False
            if self.job_id:
                update_job(self.job_id, status=self.status, stats=self._job_stats(), message=self.message)
            events.emit("discovery_status", self.snapshot())
