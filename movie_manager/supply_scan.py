"""Diagnostic "Scan provider supply" action.

Unlike real discovery, a supply scan never writes to the movies table and
never queues a download -- it only searches, fetches metadata, and does a
safe HEAD/ranged probe on the direct file URL of anything that looks like a
genuine, rights-clear candidate. Its purpose is purely to answer: does this
provider realistically have enough qualifying content for this language?

It shares the provider_scan_cache table with the real Internet Archive
discovery loop in discovery.py, so a candidate already evaluated by either
one is never re-fetched by the other.
"""
import logging
import threading
import time

import requests

from .db import get_provider_cache_entry, movie_exists, set_provider_cache_entry
from .events import events
from .internet_archive import ARCHIVE_NETWORK_RETRY_SECONDS, InternetArchiveError, InternetArchiveProvider
from .language_profiles import PROFILES
from .source_adapters import DirectHttpAdapter

logger = logging.getLogger(__name__)

SUPPLY_SCAN_PROVIDERS = {"internet_archive"}


class ArchiveTemporaryFailure(RuntimeError):
    """Mirrors discovery.ArchiveTemporaryFailure -- kept as a separate class
    so this module has no import-time dependency on discovery.py."""


class SupplyScanController:
    def __init__(self):
        self._thread = None
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.status = "IDLE"
        self.language = "yoruba"
        self.provider_name = "internet_archive"
        self.max_candidates = 250
        self.message = ""
        self.network_wait = False
        self.counters = self._fresh_counters()
        self.qualifying = []

    @staticmethod
    def _fresh_counters():
        return {
            "candidates_searched": 0,
            "metadata_checked": 0,
            "qualifying_60min": 0,
            "source_ready": 0,
            "under_60_minutes": 0,
            "wrong_language": 0,
            "not_movie": 0,
            "compilation_trailer_etc": 0,
            "duplicate": 0,
            "ambiguous_rights": 0,
            "no_usable_video_file": 0,
            "temporary_archive_errors": 0,
            "permanent_provider_errors": 0,
        }

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "language": self.language,
                "provider": self.provider_name,
                "max_candidates": self.max_candidates,
                "message": self.message,
                "network_wait": self.network_wait,
                "counters": dict(self.counters),
                "qualifying": list(self.qualifying),
            }

    def start(self, language, provider_name, max_candidates):
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A supply scan is already running.")
            if provider_name not in SUPPLY_SCAN_PROVIDERS:
                raise ValueError(f"Supply scan does not support provider '{provider_name}' yet.")
            if not PROFILES.get(language):
                raise ValueError(f"Language '{language}' is not known.")
            self.language = language
            self.provider_name = provider_name
            self.max_candidates = max(1, min(2000, int(max_candidates)))
            self._pause.clear()
            self._stop.clear()
            self.counters = self._fresh_counters()
            self.qualifying = []
            self.status = "RUNNING"
            self.message = "Starting supply scan..."
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            events.emit("supply_scan_status", self.snapshot())

    def pause(self):
        with self._lock:
            if self.status == "RUNNING":
                self._pause.set()
                self.status = "PAUSED"
                self.message = "Paused by user."
                events.emit("supply_scan_status", self.snapshot())

    def resume(self):
        with self._lock:
            if self.status == "PAUSED":
                self._pause.clear()
                self.status = "RUNNING"
                self.message = "Resumed."
                events.emit("supply_scan_status", self.snapshot())

    def stop(self):
        self._stop.set()
        self._pause.clear()
        with self._lock:
            if self.status not in {"IDLE", "COMPLETED", "STOPPED", "ERROR"}:
                self.status = "STOPPING"
                self.message = "Stopping safely..."
                events.emit("supply_scan_status", self.snapshot())

    def _wait_if_paused(self):
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.25)

    def _budget_left(self):
        with self._lock:
            return self.counters["candidates_searched"] < self.max_candidates

    def _archive_request(self, func, *args, max_attempts=4, **kwargs):
        """Same bounded backoff philosophy as
        DiscoveryController._archive_request: retries temporary failures
        with Archive's own backoff table, gives up after max_attempts
        (raising ArchiveTemporaryFailure) rather than retrying forever, so
        one flaky candidate can never stall the whole scan."""
        attempt = 0
        while not self._stop.is_set():
            self._wait_if_paused()
            try:
                result = func(*args, **kwargs)
                with self._lock:
                    recovered = self.network_wait
                    self.network_wait = False
                if recovered:
                    events.emit("supply_scan_status", self.snapshot())
                return result
            except InternetArchiveError:
                raise
            except requests.RequestException as exc:
                attempt += 1
                with self._lock:
                    self.counters["temporary_archive_errors"] += 1
                if attempt >= max_attempts:
                    raise ArchiveTemporaryFailure(str(exc)) from exc
                delay = ARCHIVE_NETWORK_RETRY_SECONDS[min(attempt - 1, len(ARCHIVE_NETWORK_RETRY_SECONDS) - 1)]
                with self._lock:
                    self.message = f"Internet Archive unavailable. Retrying automatically in {delay}s..."
                    self.network_wait = True
                events.emit("network_wait", {"scope": "supply_scan", "delay": delay, "error": str(exc)})
                events.emit("supply_scan_status", self.snapshot())
                for _ in range(delay * 4):
                    if self._stop.is_set():
                        return None
                    self._wait_if_paused()
                    time.sleep(0.25)
        return None

    def _run(self):
        try:
            self._run_internet_archive()
            if self._stop.is_set():
                with self._lock:
                    self.status = "STOPPED"
                    self.message = "Stopped safely."
            else:
                with self._lock:
                    self.status = "COMPLETED"
                    c = self.counters
                    self.message = (
                        f"Scan complete: {c['candidates_searched']} candidates searched, "
                        f"{c['qualifying_60min']} qualifying, {c['source_ready']} SOURCE_READY."
                    )
        except Exception as exc:
            with self._lock:
                self.status = "ERROR"
                self.message = str(exc)
            events.emit("error", {"scope": "supply_scan", "message": str(exc)})
        finally:
            with self._lock:
                self.network_wait = False
            events.emit("supply_scan_status", self.snapshot())

    def _tally_and_probe(self, provider, movie, result):
        """Counts one evaluated candidate and, for a rights-clear ACCEPTED
        candidate, does a safe HEAD/ranged probe (never a full download) to
        determine SOURCE_READY. Mutates and returns movie for cache writes."""
        reason = movie.get("rejection_reason") or ""
        with self._lock:
            if result == "ACCEPTED":
                self.counters["qualifying_60min"] += 1
            elif result == "UNDER_DURATION":
                self.counters["under_60_minutes"] += 1
            elif result == "WRONG_LANGUAGE":
                self.counters["wrong_language"] += 1
            elif result == "NOT_MOVIE":
                if reason.startswith("COMPILATION_") or reason.startswith("BLOCKED_TERM:"):
                    self.counters["compilation_trailer_etc"] += 1
                elif reason == "NO_VIDEO_FILE":
                    self.counters["no_usable_video_file"] += 1
                else:
                    self.counters["not_movie"] += 1

        if result != "ACCEPTED":
            return movie

        rights_clear = bool(movie.get("_rights_clear"))
        if not rights_clear:
            with self._lock:
                self.counters["ambiguous_rights"] += 1
            movie["source_status"] = "SOURCE_INVALID"
            movie["source_error"] = "Ambiguous or missing rights metadata; not auto-queued."
            return movie

        probe = DirectHttpAdapter().validate_source(movie.get("download_url") or "")
        if probe.get("verified"):
            movie["source_status"] = "SOURCE_READY"
            with self._lock:
                self.counters["source_ready"] += 1
                self.qualifying.append({
                    "identifier": movie["video_id"].split(":", 1)[-1],
                    "title": movie.get("title"),
                    "duration_seconds": movie.get("duration_seconds"),
                    "licence": movie.get("licence"),
                    "download_url": movie.get("download_url"),
                    "details_url": movie.get("youtube_url"),
                })
        else:
            movie["source_status"] = "SOURCE_INVALID"
            movie["source_error"] = probe.get("error")
        return movie

    def _run_internet_archive(self):
        provider = InternetArchiveProvider()
        profile = PROFILES.get(self.language) or {}
        queries = profile.get("archive_queries") or []
        min_seconds = 60 * 60
        seen = set()

        for query in queries:
            if self._stop.is_set() or not self._budget_left():
                break
            page = 1
            while not self._stop.is_set() and self._budget_left():
                self._wait_if_paused()
                with self._lock:
                    self.message = f"Searching Internet Archive: {query!r} (page {page})"
                events.emit("supply_scan_status", self.snapshot())

                try:
                    search_result = self._archive_request(provider.search_page, query, page)
                except InternetArchiveError as exc:
                    logger.warning("Supply scan: query failed permanently: %s (%s)", query, exc)
                    break
                except ArchiveTemporaryFailure as exc:
                    logger.warning("Supply scan: query temporarily unreachable: %s (%s)", query, exc)
                    break
                if search_result is None:
                    break
                docs, num_found = search_result
                if not docs:
                    break

                for doc in docs:
                    if self._stop.is_set() or not self._budget_left():
                        break
                    self._wait_if_paused()
                    identifier = doc.get("identifier")
                    if not identifier:
                        continue

                    with self._lock:
                        self.counters["candidates_searched"] += 1

                    if identifier in seen or movie_exists(self.language, f"internet_archive:{identifier}"):
                        with self._lock:
                            self.counters["duplicate"] += 1
                        continue
                    seen.add(identifier)

                    cache_entry = get_provider_cache_entry(provider.name, self.language, identifier)
                    if cache_entry and cache_entry["status"] != "ERROR_TEMPORARY":
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
                            with self._lock:
                                self.counters["permanent_provider_errors"] += 1
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
                        with self._lock:
                            self.counters["metadata_checked"] += 1

                        movie = self._tally_and_probe(provider, movie, result)
                        cache_fields = provider.cache_payload(movie, result)
                        if cache_fields:
                            set_provider_cache_entry(provider.name, self.language, identifier, **cache_fields)
                        continue

                    # cache hit -- still tally, and re-probe rights-clear items
                    # fresh each run (file availability can change over time)
                    # without re-fetching metadata.
                    self._tally_and_probe(provider, movie, result)

                page += 1


supply_scan = SupplyScanController()
