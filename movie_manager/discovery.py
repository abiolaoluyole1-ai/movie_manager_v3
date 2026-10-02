import logging
import os
import threading
import time
from typing import Dict, List

import requests

from .config import NETWORK_RETRY_SECONDS
from .db import (
    apply_source_resolution, count_downloadable, count_movies, create_job, mark_youtube_download_ready,
    find_probable_title_duplicate, get_movie_id, get_provider_cache_entry, get_search_state,
    get_setting, get_latest_job, get_latest_source_query, movie_exists, save_search_state,
    set_provider_cache_entry, update_job, upsert_movie, upsert_movie_with_target_guard,
)
from .content_rules import find_blocked_term, find_compilation_phrase, is_language_candidate
from .events import activity, events, redact_secrets
from .internet_archive import ARCHIVE_NETWORK_RETRY_SECONDS, InternetArchiveError, InternetArchiveProvider
from .language_profiles import PROFILES, build_search_plans
from .source_adapters import resolver
from .utils import format_duration, parse_iso8601_duration, normalise_title

DISCOVERY_PROVIDERS = {"youtube"}

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
ACCEPTED_STATUSES = ["ACCEPTED", "QUEUED", "DOWNLOADING", "DOWNLOADED"]
TEMPORARY_API_STATUSES = {429, 500, 502, 503, 504}
# YouTube itself stops paging after roughly 10 pages (~500 results); this only guards
# against a misbehaving token chain looping forever.
MAX_PAGES_PER_PLAN = 15
QUOTA_REASONS = {"quotaexceeded", "dailylimitexceeded", "ratelimitexceeded", "userratelimitexceeded"}
logger = logging.getLogger(__name__)


class ArchiveTemporaryFailure(RuntimeError):
    """Internet Archive kept failing on a transient error (timeout,
    connection error, 429/500/502/503/504) even after the bounded backoff
    retries were exhausted. Distinct from InternetArchiveError (a permanent,
    non-retryable failure) so the caller can cache this candidate as
    "temporary -- retry later" instead of "permanently rejected"."""


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


def _friendly_error(exc):
    """A human-readable reason for the Live Log / status line."""
    if isinstance(exc, YouTubeAPIError) and (exc.reason or "").lower() in QUOTA_REASONS:
        return (
            "YouTube's daily search quota is used up. Discovery can continue once the quota "
            "resets (midnight Pacific Time). Everything found so far is saved."
        )
    return redact_secrets(str(exc) or exc.__class__.__name__)


def _result_line(result, movie):
    """(level, text) for one evaluated candidate in the Live Log."""
    title = movie.get("title") or movie.get("video_id")
    minutes = int(movie.get("duration_seconds") or 0) // 60
    reason = movie.get("rejection_reason") or ""
    if result == "ACCEPTED":
        return "good", f"✓ Accepted: {title} — {format_duration(movie.get('duration_seconds'))}"
    if result == "TARGET_REACHED":
        return "info", f"Target already reached, kept for later: {title}"
    if result == "UNDER_DURATION":
        return "dim", f"✕ Too short ({minutes}m): {title}"
    if result == "WRONG_LANGUAGE":
        return "dim", f"✕ Wrong language: {title}"
    if result == "DUPLICATE":
        return "dim", f"✕ Duplicate: {title}"
    if reason.startswith("BLOCKED_TERM:"):
        return "dim", f"✕ Rejected ({reason.split(':', 1)[1]}): {title}"
    if reason.startswith("COMPILATION_"):
        return "dim", f"✕ Rejected (compilation): {title}"
    return "dim", f"✕ Rejected: {title}"


class DiscoveryController:
    def __init__(self):
        self._thread = None
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.status = "IDLE"
        self.language = "yoruba"
        self.provider = "youtube"
        self.target = 30
        self.stats = self._fresh_stats()
        self.current_query = ""
        self.message = ""
        self.job_id = None
        self.network_wait = False
        self.downloadable_only = False

    def _target_progress_count(self, language=None):
        language = language or self.language
        if self.downloadable_only:
            return count_downloadable(language, ACCEPTED_STATUSES)
        return count_movies(language, ACCEPTED_STATUSES)

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
            "temporary_provider_errors": 0,
            "permanent_provider_errors": 0,
            "cache_hits": 0,
        }

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "language": self.language,
                "provider": self.provider,
                "target": self.target,
                "current_query": self.current_query,
                "message": self.message,
                "stats": dict(self.stats),
                "network_wait": self.network_wait,
            }

    def _job_stats(self):
        stats = dict(self.stats)
        stats["current_query"] = self.current_query
        stats["provider"] = self.provider
        return stats

    def _log(self, message, level="info"):
        activity.add(message, level, scope="discovery", language=self.language)

    def restore_latest(self, language):
        """Show `language`'s own last discovery run when no worker is active.

        The newest job is used even if it did little work: skipping "empty"
        jobs used to make a run that had just finished get replaced by some
        older job's status and counters on the next refresh. A language with no
        history at all starts from a clean idle state, never another
        language's numbers.
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.snapshot()

            job = get_latest_job("DISCOVERY", language)
            if not job:
                self.language = language
                self.stats = self._fresh_stats()
                self.status = "IDLE"
                self.current_query = ""
                self.message = ""
                self.job_id = None
                self.network_wait = False
                return self.snapshot()

            restored = self._fresh_stats()
            for key in restored:
                restored[key] = int(job["stats"].get(key, 0) or 0)
            self.stats = restored
            self.status = job["status"]
            self.language = job["language"]
            self.provider = job["stats"].get("provider", "youtube")
            self.target = int(job["target"] or self.target)
            self.current_query = (
                job["stats"].get("current_query") or get_latest_source_query(language)
            )
            self.message = job.get("message") or "Discovery completed."
            self.job_id = None
            self.network_wait = False
            return self.snapshot()

    def start(self, language: str, target: int, provider: str = "youtube"):
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self.status == "PAUSED":
                    self.target = target
                    self._pause.clear()
                    self.status = "RUNNING"
                    self._log("Discovery resumed.", "good")
                    events.emit("discovery_status", self.snapshot())
                    return
                raise RuntimeError("Discovery is already running.")
            profile = PROFILES.get(language)
            if not profile or not profile.get("enabled"):
                raise ValueError(f"Language '{language}' is not enabled yet.")
            if provider not in DISCOVERY_PROVIDERS:
                raise ValueError(f"Unknown discovery provider '{provider}'.")
            self.language = language
            self.provider = provider
            self.target = max(1, int(target))
            self._pause.clear()
            self._stop.clear()
            self.stats = self._fresh_stats()
            self.network_wait = False
            self.downloadable_only = get_setting("count_only_downloadable", "0") == "1"
            accepted_now = self._target_progress_count(language)
            if accepted_now >= self.target:
                self.restore_latest(language)
                self.target = max(1, int(target))
                self.language = language
                self.status = "COMPLETED"
                self.message = f"Target already satisfied: {accepted_now}/{self.target}"
                self._log(
                    f"{PROFILES[language]['label']} target already reached: {accepted_now} / {self.target}. "
                    "Raise the target to find more.", "info",
                )
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
                self._log("Discovery paused.", "warn")
                if self.job_id:
                    update_job(self.job_id, status="PAUSED", stats=self._job_stats(), message=self.message)
                events.emit("discovery_status", self.snapshot())

    def resume(self):
        with self._lock:
            if self.status == "PAUSED":
                self._pause.clear()
                self.status = "RUNNING"
                self.message = "Resumed."
                self._log("Discovery resumed.", "good")
                if self.job_id:
                    update_job(self.job_id, status="RUNNING", stats=self._job_stats(), message=self.message)
                events.emit("discovery_status", self.snapshot())

    def stop(self):
        self._stop.set()
        self._pause.clear()
        with self._lock:
            if self.status in {"RUNNING", "PAUSED"}:
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
                reason = redact_secrets(exc)
                logger.warning("YouTube request failed, retrying in %ss: %s", delay, reason)
                self._log(f"Network or YouTube problem. Retrying automatically in {delay}s...", "warn")
                events.emit("network_wait", {"scope": "discovery", "delay": delay, "error": reason})
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
            elif result == "PROVIDER_ERROR":
                self.stats["permanent_provider_errors"] += 1

    def _accept_and_resolve(self, movie, result, resolution_override=None):
        """Shared accept+source-resolution chokepoint used by every
        provider's search cycle (YouTube, Internet Archive, ...).

        Atomically enforces the exact-target guard via
        upsert_movie_with_target_guard, then resolves (or applies a
        provider-forced) download source via the same
        apply_source_resolution atomic write used everywhere else, so every
        provider gets identical exact-target and source-status guarantees.

        resolution_override lets a provider force a specific source
        resolution (e.g. Internet Archive marking an ambiguous-rights item
        SOURCE_INVALID without ever probing/queuing its file) instead of
        running the normal adapter resolver.

        Returns (movie, result) reflecting what was actually stored.
        """
        # The catalogue target is always an exact ceiling. A former
        # downloadable-only branch bypassed this guard while sources resolved.
        stored_status = upsert_movie_with_target_guard(movie, ACCEPTED_STATUSES, self.target)
        if stored_status != "ACCEPTED":
            result = "TARGET_REACHED"
        movie = {**movie, "status": stored_status}

        if stored_status == "ACCEPTED" and not self._stop.is_set():
            movie_id = get_movie_id(self.language, movie["video_id"])
            if movie_id is not None:
                # YouTube sources are stable identities, not fragile direct CDN URLs.
                mark_youtube_download_ready(movie_id)
                movie["id"] = movie_id
                movie["source_status"] = "SOURCE_READY"
                movie["source_provider"] = "youtube"
                movie["download_status"] = "READY"
                events.emit("source_resolved", {
                    "movie_id": movie_id,
                    "source_status": "SOURCE_READY", "provider": "youtube", "error": None,
                })
        return movie, result

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

        if not is_language_candidate(
            self.language, snippet.get("defaultAudioLanguage") or "", snippet.get("defaultLanguage") or "",
            title, description, snippet.get("channelTitle"), query,
        ):
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = "WRONG_LANGUAGE"
            return movie, "WRONG_LANGUAGE"

        title_lowered = title.lower()
        compilation = find_compilation_phrase(title_lowered, description.lower())
        if compilation:
            scope, phrase = compilation
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = f"COMPILATION_{scope}:{phrase}"
            return movie, "NOT_MOVIE"

        blocked = find_blocked_term(lowered)
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
            if self.provider == "internet_archive":
                self._run_archive()
            else:
                self._run_youtube()

            if self._stop.is_set():
                with self._lock:
                    self.status = "STOPPED"
                    self.message = "Stopped safely. Existing progress is saved."
                self._log(
                    f"Discovery stopped. Progress is saved: {self._target_progress_count()} / {self.target}.",
                    "warn",
                )
            elif self.status == "RUNNING":
                # Never fall back to an unexplained idle state.
                with self._lock:
                    self.status = "ERROR"
                    self.message = "Discovery ended unexpectedly without a result. Please try again."
                self._log(self.message, "bad")

        except Exception as exc:
            # Never swallow a worker failure: full traceback to the terminal/log
            # file, plain-English reason to the Live Log and status line.
            logger.exception("Discovery worker crashed (language=%s)", self.language)
            message = _friendly_error(exc)
            with self._lock:
                self.status = "ERROR"
                self.message = message
            self._log(f"Discovery stopped by an error: {message}", "bad")
            events.emit("error", {"scope": "discovery", "message": message})
        finally:
            with self._lock:
                self.network_wait = False
            if self.job_id:
                update_job(self.job_id, status=self.status, stats=self._job_stats(), message=self.message)
            events.emit("discovery_status", self.snapshot())

    def _search_params(self, key, profile, plan, token):
        params = {
            "part": "snippet",
            "type": "video",
            "maxResults": 50,
            "q": plan["query"],
            "regionCode": profile["region_code"],
            "videoDuration": "long",
            "safeSearch": "moderate",
            "key": key,
        }
        if profile.get("relevance_language"):
            params["relevanceLanguage"] = profile["relevance_language"]
        params.update(plan["params"])
        if token:
            params["pageToken"] = token
        return params

    def _process_page(self, plan, payload, min_seconds):
        """Evaluate one page of search results. Returns (accepted_here, finished).

        `finished` is False when the page was cut short (target reached or
        stop requested), so the caller keeps the same page position instead of
        skipping the candidates that were never looked at.
        """
        query = plan["query"]
        items = payload.get("items", [])
        ids = [i.get("id", {}).get("videoId") for i in items if i.get("id", {}).get("videoId")]
        new_ids = [vid for vid in ids if not movie_exists(self.language, vid)]
        existing_count = len(ids) - len(new_ids)
        with self._lock:
            self.stats["duplicates_skipped"] += existing_count
            self.stats["candidates_scanned"] += len(ids)

        if ids:
            self._log(
                f"Scanned {len(ids)} results: {existing_count} already in your catalogue, "
                f"{len(new_ids)} new to check."
            )
        else:
            self._log("This search returned no results.")

        accepted_here = rejected_here = 0
        finished = True
        details = self._fetch_details(new_ids)
        for item in details:
            if self._stop.is_set() or self._target_progress_count() >= self.target:
                finished = False
                break
            self._wait_if_paused()
            movie, result = self._evaluate(item, query, min_seconds)

            if result == "ACCEPTED":
                # Atomically re-checks accepted (or downloadable, in
                # downloadable-only mode) count against target at write
                # time, so a concurrent writer -- another thread in this
                # process, or a separate process -- can never push the
                # total past target.
                movie, result = self._accept_and_resolve(movie, result)
            else:
                upsert_movie(movie)

            self._record_result(result)
            if result == "ACCEPTED":
                accepted_here += 1
            elif result != "TARGET_REACHED":
                rejected_here += 1
            level, line = _result_line(result, movie)
            self._log(line, level)

            events.emit("movie_processed", {
                "result": result,
                "movie": movie,
                "stats": dict(self.stats),
                "target": self.target,
            })

        if accepted_here:
            self._log(
                f"This page: {accepted_here} accepted, {rejected_here} rejected, "
                f"{existing_count} already known.", "good",
            )
        elif finished:
            self._log(
                f"No new movies from this page ({existing_count} already known, {rejected_here} rejected). "
                "Moving to the next search..."
            )
        return accepted_here, finished

    def _run_youtube(self):
        key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if not key or key == "PASTE_YOUR_PRIVATE_KEY_HERE":
            raise RuntimeError("YouTube API key is missing. Add it to the .env file.")

        profile = PROFILES[self.language]
        label = profile["label"]
        min_seconds = 60 * 60
        plans = build_search_plans(self.language)
        self._log(
            f"{label} discovery started: {self._target_progress_count()} of {self.target} in your "
            f"catalogue, {len(plans)} search strategies available."
        )

        search_pass = 0
        plan_pages = {}
        while not self._stop.is_set():
            accepted_now = count_movies(self.language, ACCEPTED_STATUSES)
            progress_now = self._target_progress_count()
            with self._lock:
                self.stats["accepted"] = accepted_now
            if progress_now >= self.target:
                with self._lock:
                    self.status = "COMPLETED"
                    self.message = f"Discovery complete: {progress_now} / {self.target}"
                self._log(self.message, "good")
                break

            search_pass += 1
            pages_searched = pass_accepted = 0
            if search_pass > 1:
                self._log(f"Starting search pass {search_pass} with the strategies that still have more pages...")

            for plan in plans:
                if self._stop.is_set():
                    break
                self._wait_if_paused()
                if self._target_progress_count() >= self.target:
                    break

                token, exhausted = get_search_state(self.language, plan["key"])
                if exhausted:
                    continue

                with self._lock:
                    self.current_query = plan["label"]
                    self.message = f"Searching: {plan['label']}"
                self._log(
                    f"Searching YouTube for {label} movies: {plan['label']}"
                    + (" — next page" if token else "")
                )
                events.emit("discovery_status", self.snapshot())

                try:
                    response = self._search(self._search_params(key, profile, plan, token))
                except YouTubeAPIError as exc:
                    if token and exc.status_code == 400 and "pagetoken" in (exc.reason or "").lower():
                        self._log(
                            f"The saved page position for \"{plan['label']}\" is no longer valid. "
                            "Marking that search as finished.", "warn",
                        )
                        save_search_state(self.language, plan["key"], None, True)
                        continue
                    raise
                if response is None:
                    break
                pages_searched += 1

                payload = response.json()
                accepted_here, finished = self._process_page(plan, payload, min_seconds)
                pass_accepted += accepted_here

                next_token = payload.get("nextPageToken")
                plan_pages[plan["key"]] = plan_pages.get(plan["key"], 0) + 1
                if plan_pages[plan["key"]] >= MAX_PAGES_PER_PLAN:
                    next_token = None
                if finished:
                    save_search_state(self.language, plan["key"], next_token, not bool(next_token))
                    if not next_token:
                        self._log(f"Search finished, no more pages: {plan['label']}")
                else:
                    # Keep the page we were on; its unchecked candidates are
                    # looked at first on the next run.
                    save_search_state(self.language, plan["key"], token, False)
                if self.job_id:
                    update_job(self.job_id, stats=self._job_stats(), message=self.message)

            if self._stop.is_set() or self._target_progress_count() >= self.target:
                continue

            if pages_searched == 0:
                progress_now = self._target_progress_count()
                with self._lock:
                    stats = dict(self.stats)
                    self.status = "EXHAUSTED"
                    self.message = (
                        f"Search pool exhausted. Found {progress_now} of {self.target}. "
                        "No additional unique qualifying movies were found."
                    )
                self._log(self.message, "warn")
                rejected = (
                    stats["rejected_under_duration"] + stats["rejected_not_movie"]
                    + stats["rejected_wrong_language"]
                )
                self._log(
                    f"This run checked {stats['candidates_scanned']} results: "
                    f"{stats['duplicates_skipped']} already known, {rejected} rejected.", "warn",
                )
                break

            self._log(
                f"Search pass {search_pass} done: {pass_accepted} new, "
                f"{self._target_progress_count()} / {self.target} in your catalogue."
            )

    def _archive_request(self, func, *args, max_attempts=4, **kwargs):
        """Calls an InternetArchiveProvider method with a bounded retry/
        backoff: temporary network/HTTP failures (timeout, connection error,
        429/500/502/503/504) are retried with Archive's own (longer, more
        patient) backoff table, flagging network_wait for the UI exactly
        like YouTube discovery does. Unlike YouTube's _request() (which
        retries forever), this gives up after max_attempts and raises
        ArchiveTemporaryFailure -- so one persistently-flaky Archive item
        can never block the whole scan indefinitely; the caller skips it
        and moves on, and it stays eligible for retry on a later run.
        Permanent failures (InternetArchiveError) propagate immediately."""
        attempt = 0
        while not self._stop.is_set():
            self._wait_if_paused()
            try:
                result = func(*args, **kwargs)
                with self._lock:
                    self.stats["api_requests"] += 1
                    recovered = self.network_wait
                    self.network_wait = False
                if recovered:
                    events.emit("discovery_status", self.snapshot())
                return result
            except InternetArchiveError:
                raise
            except requests.RequestException as exc:
                attempt += 1
                with self._lock:
                    self.stats["network_retries"] += 1
                    self.stats["temporary_provider_errors"] += 1
                if attempt >= max_attempts:
                    raise ArchiveTemporaryFailure(str(exc)) from exc
                delay = ARCHIVE_NETWORK_RETRY_SECONDS[min(attempt - 1, len(ARCHIVE_NETWORK_RETRY_SECONDS) - 1)]
                with self._lock:
                    self.message = f"Internet Archive unavailable. Retrying automatically in {delay}s..."
                    self.network_wait = True
                events.emit("network_wait", {"scope": "discovery", "delay": delay, "error": str(exc)})
                events.emit("discovery_status", self.snapshot())
                for _ in range(delay * 4):
                    if self._stop.is_set():
                        return None
                    self._wait_if_paused()
                    time.sleep(0.25)
        return None

    def _run_archive(self):
        provider = InternetArchiveProvider()
        profile = PROFILES[self.language]
        queries = profile.get("archive_queries") or []
        min_seconds = 60 * 60

        while not self._stop.is_set():
            progress_now = self._target_progress_count()
            with self._lock:
                self.stats["accepted"] = count_movies(self.language, ACCEPTED_STATUSES)
            if progress_now >= self.target:
                with self._lock:
                    self.status = "COMPLETED"
                    self.message = f"Target reached: {progress_now}/{self.target}"
                break

            progressed = False
            for query in queries:
                if self._stop.is_set():
                    break
                self._wait_if_paused()
                if self._target_progress_count() >= self.target:
                    break

                state_key = f"archive:{query}"
                token, exhausted = get_search_state(self.language, state_key)
                if exhausted:
                    continue
                page = int(token) if token else 1

                with self._lock:
                    self.current_query = query
                    self.message = f"Searching Internet Archive: {query}"
                events.emit("discovery_status", self.snapshot())

                try:
                    search_result = self._archive_request(provider.search_page, query, page)
                except InternetArchiveError as exc:
                    logger.warning("Internet Archive query failed permanently: %s (%s)", query, exc)
                    save_search_state(self.language, state_key, None, True)
                    continue
                except ArchiveTemporaryFailure as exc:
                    logger.warning("Internet Archive query temporarily unreachable: %s (%s)", query, exc)
                    continue
                if search_result is None:
                    break
                docs, num_found = search_result
                progressed = True

                identifiers = [d.get("identifier") for d in docs if d.get("identifier")]
                new_candidates = [
                    (identifier, doc) for identifier, doc in zip(identifiers, docs)
                    if not movie_exists(self.language, f"internet_archive:{identifier}")
                ]
                existing_count = len(identifiers) - len(new_candidates)
                with self._lock:
                    self.stats["duplicates_skipped"] += existing_count
                    self.stats["candidates_scanned"] += len(identifiers)

                for identifier, doc in new_candidates:
                    if self._stop.is_set():
                        break
                    self._wait_if_paused()

                    cache_entry = get_provider_cache_entry(provider.name, self.language, identifier)
                    if cache_entry and cache_entry["status"] != "ERROR_TEMPORARY":
                        with self._lock:
                            self.stats["cache_hits"] += 1
                        movie, result = provider.movie_from_cache(
                            self.language, identifier, doc, query, cache_entry
                        )
                    else:
                        try:
                            eval_result = self._archive_request(
                                provider.evaluate_candidate, identifier, doc, self.language, query, min_seconds
                            )
                        except InternetArchiveError as exc:
                            set_provider_cache_entry(
                                provider.name, self.language, identifier, "ERROR_PERMANENT",
                                result_code="PROVIDER_ERROR", reason=str(exc)[:500],
                            )
                            self._record_result("PROVIDER_ERROR")
                            continue
                        except ArchiveTemporaryFailure as exc:
                            set_provider_cache_entry(
                                provider.name, self.language, identifier, "ERROR_TEMPORARY",
                                reason=str(exc)[:500],
                            )
                            continue
                        if eval_result is None:
                            break
                        movie, result = eval_result
                        cache_fields = provider.cache_payload(movie, result)
                        if cache_fields:
                            set_provider_cache_entry(provider.name, self.language, identifier, **cache_fields)

                    if movie is None:
                        continue
                    progressed = True

                    if result == "ACCEPTED":
                        resolution_override = None
                        if not movie.get("_rights_clear"):
                            resolution_override = {
                                "source_status": "SOURCE_INVALID",
                                "provider": provider.name,
                                "error": "Ambiguous or missing rights metadata; not auto-queued.",
                                "download_url": None,
                            }
                        movie, result = self._accept_and_resolve(
                            movie, result, resolution_override=resolution_override
                        )
                    else:
                        upsert_movie(movie)

                    self._record_result(result)
                    events.emit("movie_processed", {
                        "result": result, "movie": movie, "stats": dict(self.stats), "target": self.target,
                    })

                    if self._target_progress_count() >= self.target:
                        break

                has_more = (page * 50) < num_found
                next_state = str(page + 1) if has_more else None
                save_search_state(self.language, state_key, next_state, not has_more)
                if self.job_id:
                    update_job(self.job_id, stats=self._job_stats(), message=self.message)

            if self._target_progress_count() >= self.target:
                continue

            if not progressed:
                with self._lock:
                    self.status = "COMPLETED"
                    self.message = (
                        "Internet Archive search sources are exhausted before the target was reached."
                    )
                break
