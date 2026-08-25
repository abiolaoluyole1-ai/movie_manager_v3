import os
import threading
import time
from pathlib import Path

import requests

from .config import (
    CONCURRENCY_DEFAULT, CONCURRENCY_MAX, CONCURRENCY_MIN,
    MIN_FREE_DISK_GB_DEFAULT, NETWORK_RETRY_SECONDS, YOUTUBE_BLOCKED_MESSAGE,
    clamp_min_free_disk_gb, clamp_youtube_browser, is_youtube_blocked_error,
)
from .db import get_setting, next_download_ready, update_download
from .events import events
from .utils import extension_from_url, free_disk_space_gb, safe_filename, is_http_url


class YtDlpDownloadBackend:
    """yt-dlp integration owned by Movie Manager, with throttled progress."""
    def __init__(self, controller):
        self.controller = controller

    @staticmethod
    def _format(quality):
        limit = {"1080": 1080, "720": 720, "480": 480}.get(str(quality))
        if not limit:
            return "bestvideo*+bestaudio/best"
        return f"bestvideo*[height<={limit}]+bestaudio/best[height<={limit}]/best"

    def download(self, movie, worker_id, final, part):
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("YouTube downloads unavailable: yt-dlp is not installed.") from exc
        import shutil
        if not shutil.which("ffmpeg"):
            raise RuntimeError("YouTube downloads unavailable\nFFmpeg was not found.")
        last = [0.0]
        def hook(event):
            if self.controller._stop.is_set():
                raise yt_dlp.utils.DownloadCancelled("Stopped by user")
            self.controller._wait_if_paused()
            state = event.get("status")
            if state == "downloading":
                now = time.monotonic()
                if now-last[0] < 1.0:
                    return
                last[0] = now
                done, total = event.get("downloaded_bytes", 0), event.get("total_bytes") or event.get("total_bytes_estimate")
                speed, eta = event.get("speed") or 0, event.get("eta")
                update_download(movie["id"], download_status="DOWNLOADING", bytes_downloaded=done,
                                total_bytes=total, download_speed_bps=speed, download_eta_seconds=eta)
                self.controller._update_job(movie, worker_id, stage="DOWNLOADING", bytes_downloaded=done,
                                            total_bytes=total, speed_bps=speed, eta_seconds=eta)
                events.emit("download_progress", {"movie_id": movie["id"], "bytes_downloaded": done,
                    "total_bytes": total, "speed_bps": speed, "eta_seconds": eta})
            elif state == "postprocessing":
                self.controller._update_job(movie, worker_id, stage="MERGING")

        quality = get_setting("download_quality", "1080")
        options = {
            "format": self._format(quality), "outtmpl": str(final.with_suffix(".%(ext)s")),
            "paths": {"home": str(final.parent), "temp": str(final.parent)}, "noplaylist": True,
            "continuedl": True, "nopart": False, "merge_output_format": "mp4", "remuxvideo": "mp4",
            "ffmpeg_location": shutil.which("ffmpeg"), "progress_hooks": [hook], "quiet": True,
            "no_warnings": True, "retries": 3, "fragment_retries": 3,
        }
        browser = None
        if get_setting("youtube_use_browser_session", "0") == "1":
            browser = clamp_youtube_browser(get_setting("youtube_browser", "chrome"))
            # Reads that browser's own local cookie store at runtime (yt-dlp's
            # --cookies-from-browser) -- nothing is copied, exported or logged.
            options["cookiesfrombrowser"] = (browser,)
        self.controller._update_job(movie, worker_id, stage="PREPARING")
        update_download(movie["id"], download_status="DOWNLOADING", file_path=str(part), download_error=None)
        with yt_dlp.YoutubeDL(options) as ydl:
            if browser:
                try:
                    # cookiesfrombrowser is only read lazily on first use, so
                    # touch it now -- while it's still unambiguous that any
                    # failure here is a cookie/session problem, not a
                    # download one.
                    ydl.cookiejar
                except Exception as exc:
                    name = browser.capitalize()
                    raise RuntimeError(
                        f"Could not use the signed-in {name} session.\n"
                        f"Make sure you are signed into YouTube in {name} and try again."
                    ) from exc
            ydl.extract_info(movie["youtube_url"], download=True)
        # yt-dlp has selected and remuxed the final file. Never fake an MP4 rename.
        if not final.exists() or final.stat().st_size <= 0:
            candidates = sorted(final.parent.glob(f"{safe_filename(movie['title'])}*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
            candidate = next((p for p in candidates if p.suffix.lower()==".mp4" and p.stat().st_size > 0), None)
            if not candidate:
                raise RuntimeError("Output verification failed: yt-dlp did not create an MP4 file.")
            if candidate != final:
                os.replace(candidate, final)
        self.controller._update_job(movie, worker_id, stage="VERIFYING")
        return final.stat().st_size

IDLE_POLL_SECONDS = 2
DISK_CHECK_WAIT_SECONDS = 10


class DownloadController:
    """Downloader for authorised/direct HTTP(S) movie-file sources.

    Runs `concurrency` worker threads (1-7, default 3). Each worker
    independently claims one movie at a time via next_download_ready()'s
    atomic claim (so a movie is never picked up by two workers), downloads
    it start-to-finish, finalises/verifies/persists it, then immediately
    goes back for the next one -- so a freed slot is refilled without
    waiting for the rest of the batch, and each completed movie is usable
    the moment it finishes.
    """

    def __init__(self):
        self._workers = []
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.status = "IDLE"
        self.language = "yoruba"
        self.concurrency = CONCURRENCY_DEFAULT
        self.message = ""
        self.network_wait = False
        self.disk_low = False
        self.active = {}

    def snapshot(self):
        with self._lock:
            jobs = sorted(self.active.values(), key=lambda j: j.get("movie_id", 0))
            return {
                "status": self.status,
                "language": self.language,
                "concurrency": self.concurrency,
                "current_movie_id": jobs[0]["movie_id"] if jobs else None,
                "active_jobs": [dict(j) for j in jobs],
                "active_count": len(jobs),
                "message": self.message,
                "network_wait": self.network_wait,
                "disk_low": self.disk_low,
            }

    def start(self, language="yoruba", concurrency=None):
        with self._lock:
            if concurrency is None:
                concurrency = self.concurrency or CONCURRENCY_DEFAULT
            concurrency = max(CONCURRENCY_MIN, min(CONCURRENCY_MAX, int(concurrency)))

            if any(w.is_alive() for w in self._workers):
                if self.status == "PAUSED":
                    self._pause.clear()
                    self.status = "RUNNING"
                    events.emit("download_status", self.snapshot())
                return

            self.language = language
            self.concurrency = concurrency
            self._stop.clear()
            self._pause.clear()
            self.status = "RUNNING"
            self.message = f"Download workers started ({concurrency})."
            self.network_wait = False
            self.disk_low = False
            self.active = {}
            self._workers = [
                threading.Thread(target=self._run, args=(worker_id,), daemon=True)
                for worker_id in range(concurrency)
            ]
        for worker in self._workers:
            worker.start()
        events.emit("download_status", self.snapshot())

    def pause(self):
        self._pause.set()
        with self._lock:
            self.status = "PAUSED"
            self.message = "Downloads paused by user."
        events.emit("download_status", self.snapshot())

    def resume(self):
        self._pause.clear()
        with self._lock:
            self.status = "RUNNING"
            self.message = "Downloads resumed."
        events.emit("download_status", self.snapshot())

    def stop(self):
        self._stop.set()
        self._pause.clear()
        with self._lock:
            self.status = "STOPPING"
            self.message = "Stopping download workers..."
            self.network_wait = False
        events.emit("download_status", self.snapshot())

    def _wait_if_paused(self):
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.25)

    def _output_paths(self, movie):
        root = Path(get_setting("download_root"))
        folder = root / movie["language"].capitalize()
        folder.mkdir(parents=True, exist_ok=True)
        ext = ".mp4" if movie.get("download_backend") == "YTDLP" else extension_from_url(movie["download_url"])
        filename = safe_filename(movie["title"]) + ext
        final = folder / filename
        if final.exists():
            final = folder / f"{safe_filename(movie['title'])} [{movie['video_id']}]{ext}"
        part = Path(str(final) + ".part")
        return final, part

    def _disk_guard_ok(self):
        """True if the destination drive has enough free space for another
        job to start. A failure to even check (missing setting/path issue)
        never blocks downloads on its own -- only a confirmed low-space
        reading does."""
        try:
            min_gb = clamp_min_free_disk_gb(get_setting("min_free_disk_gb", str(MIN_FREE_DISK_GB_DEFAULT)))
            root = get_setting("download_root")
            if not root:
                return True
            return free_disk_space_gb(root) >= min_gb
        except Exception:
            return True

    def _enter_disk_low(self):
        with self._lock:
            if not self.disk_low:
                self.disk_low = True
                self.status = "DISK_LOW"
                self.message = "Downloads paused: low disk space. Free up space to continue."
                events.emit("download_status", self.snapshot())

    def _leave_disk_low(self):
        with self._lock:
            if self.disk_low:
                self.disk_low = False
                if not self._pause.is_set() and not self._stop.is_set():
                    self.status = "RUNNING"
                    self.message = "Disk space recovered. Resuming downloads."
                events.emit("download_status", self.snapshot())

    def _download_one(self, movie, worker_id):
        if movie.get("download_backend") == "YTDLP" and movie.get("provider") == "youtube":
            return self._download_youtube_one(movie, worker_id)
        url = movie.get("download_url") or ""
        if not is_http_url(url):
            update_download(movie["id"], download_status="FAILED", download_error="Invalid direct download URL.")
            return

        final, part = self._output_paths(movie)
        attempt = int(movie.get("retry_count") or 0)
        completed = False

        try:
            while not self._stop.is_set():
                self._wait_if_paused()
                existing = part.stat().st_size if part.exists() else 0
                headers = {"User-Agent": "MovieManagerV3/1.0"}
                if existing > 0:
                    headers["Range"] = f"bytes={existing}-"

                try:
                    with requests.get(url, headers=headers, stream=True, timeout=(20, 60), allow_redirects=True) as r:
                        if r.status_code not in {200, 206}:
                            if r.status_code in {408, 429, 500, 502, 503, 504}:
                                raise requests.RequestException(f"Temporary source error {r.status_code}")
                            raise RuntimeError(f"Download source returned HTTP {r.status_code}")

                        with self._lock:
                            recovered = self.network_wait
                            self.network_wait = False
                            if recovered:
                                self.message = f"Connection resumed: {movie['title']}"
                        if recovered:
                            events.emit("download_status", self.snapshot())

                        if existing > 0 and r.status_code == 200:
                            existing = 0
                            try:
                                part.unlink()
                            except FileNotFoundError:
                                pass

                        content_length = int(r.headers.get("Content-Length") or 0)
                        total = existing + content_length if content_length else None
                        update_download(
                            movie["id"], download_status="DOWNLOADING", status="DOWNLOADING",
                            file_path=str(part), bytes_downloaded=existing, total_bytes=total,
                            download_error=None, retry_count=attempt,
                        )
                        self._update_job(movie, worker_id, stage="DOWNLOADING",
                                          bytes_downloaded=existing, total_bytes=total, retries=attempt)

                        mode = "ab" if existing else "wb"
                        downloaded = existing
                        started = time.monotonic()
                        last_emit = 0.0

                        with open(part, mode) as f:
                            for chunk in r.iter_content(chunk_size=1024 * 512):
                                if self._stop.is_set():
                                    return
                                self._wait_if_paused()
                                if not chunk:
                                    continue
                                f.write(chunk)
                                downloaded += len(chunk)

                                now = time.monotonic()
                                elapsed = max(now - started, 0.001)
                                speed = max(downloaded - existing, 0) / elapsed
                                eta = ((total - downloaded) / speed) if total and speed > 0 else None

                                if now - last_emit >= 0.7:
                                    update_download(
                                        movie["id"], download_status="DOWNLOADING",
                                        bytes_downloaded=downloaded, total_bytes=total,
                                        download_speed_bps=speed, download_eta_seconds=eta,
                                    )
                                    self._update_job(movie, worker_id, stage="DOWNLOADING",
                                                      bytes_downloaded=downloaded, total_bytes=total,
                                                      speed_bps=speed, eta_seconds=eta, retries=attempt)
                                    events.emit("download_progress", {
                                        "movie_id": movie["id"], "bytes_downloaded": downloaded,
                                        "total_bytes": total, "speed_bps": speed, "eta_seconds": eta,
                                    })
                                    last_emit = now

                        self._update_job(movie, worker_id, stage="VERIFYING")
                        actual = part.stat().st_size
                        if total and actual < total:
                            raise requests.RequestException(f"Connection ended early ({actual}/{total} bytes).")

                        self._update_job(movie, worker_id, stage="FINALISING")
                        os.replace(part, final)
                        update_download(
                            movie["id"], download_status="DOWNLOADED", status="DOWNLOADED",
                            file_path=str(final), bytes_downloaded=actual, total_bytes=actual,
                            download_speed_bps=0, download_eta_seconds=0, download_error=None,
                        )
                        events.emit("download_complete", {"movie_id": movie["id"], "file_path": str(final)})
                        completed = True
                        return

                except (requests.RequestException, OSError) as exc:
                    delay = NETWORK_RETRY_SECONDS[min(attempt, len(NETWORK_RETRY_SECONDS)-1)]
                    attempt += 1
                    update_download(
                        movie["id"], download_status="WAITING_NETWORK",
                        download_error=str(exc), retry_count=attempt,
                    )
                    self._update_job(movie, worker_id, stage="RETRY_WAIT", retries=attempt)
                    with self._lock:
                        self.message = f"Network/source interrupted. Retrying automatically in {delay}s."
                        self.network_wait = True
                    events.emit("network_wait", {
                        "scope": "download", "movie_id": movie["id"],
                        "delay": delay, "error": str(exc),
                    })
                    events.emit("download_status", self.snapshot())
                    for _ in range(delay * 4):
                        if self._stop.is_set():
                            return
                        self._wait_if_paused()
                        time.sleep(0.25)
                except Exception as exc:
                    update_download(
                        movie["id"], download_status="FAILED", status="ACCEPTED",
                        download_error=str(exc), retry_count=attempt,
                    )
                    events.emit("error", {"scope": "download", "movie_id": movie["id"], "message": str(exc)})
                    return
        finally:
            if not completed and self._stop.is_set():
                # Movie was claimed (or mid-transfer) when Stop was
                # requested -- release it back to READY so it resumes
                # from its .part file on the next Start, instead of being
                # stuck at DOWNLOADING (unclaimable) until a restart.
                update_download(movie["id"], download_status="READY")

    def _download_youtube_one(self, movie, worker_id):
        final, part = self._output_paths(movie)
        try:
            actual = YtDlpDownloadBackend(self).download(movie, worker_id, final, part)
            update_download(movie["id"], download_status="DOWNLOADED", status="DOWNLOADED", file_path=str(final),
                bytes_downloaded=actual, total_bytes=actual, download_speed_bps=0, download_eta_seconds=0, download_error=None)
            events.emit("download_complete", {"movie_id": movie["id"], "file_path": str(final)})
        except Exception as exc:
            if self._stop.is_set():
                update_download(movie["id"], download_status="READY", status="ACCEPTED")
                return
            if is_youtube_blocked_error(exc):
                update_download(movie["id"], download_status="FAILED", status="ACCEPTED",
                                 download_error=YOUTUBE_BLOCKED_MESSAGE)
                events.emit("error", {"scope": "download", "movie_id": movie["id"],
                                       "message": YOUTUBE_BLOCKED_MESSAGE, "blocked": True})
                return
            update_download(movie["id"], download_status="FAILED", status="ACCEPTED", download_error=str(exc))
            events.emit("error", {"scope": "download", "movie_id": movie["id"], "message": str(exc)})

    def _update_job(self, movie, worker_id, **fields):
        with self._lock:
            job = self.active.get(movie["id"])
            if job is None:
                return
            prev_stage = job.get("stage")
            job.update(fields)
            new_stage = job.get("stage")
        if "stage" in fields and new_stage != prev_stage and new_stage in {"MERGING", "VERIFYING"}:
            events.emit("download_stage", {"movie_id": movie["id"], "title": movie.get("title"), "stage": new_stage})

    def _run(self, worker_id=0):
        try:
            while not self._stop.is_set():
                self._wait_if_paused()

                if not self._disk_guard_ok():
                    self._enter_disk_low()
                    for _ in range(DISK_CHECK_WAIT_SECONDS * 4):
                        if self._stop.is_set():
                            break
                        self._wait_if_paused()
                        time.sleep(0.25)
                    continue
                self._leave_disk_low()

                # Re-check right before claiming so a pause/stop requested
                # while this worker was between its last check and here
                # can't slip a claim through.
                self._wait_if_paused()
                if self._stop.is_set():
                    break

                movie = next_download_ready(self.language)
                if not movie:
                    with self._lock:
                        if not self.active and not self._pause.is_set() and not self._stop.is_set():
                            self.status = "WAITING"
                            self.message = "Waiting for an authorised local-file download source..."
                    events.emit("download_status", self.snapshot())
                    for _ in range(IDLE_POLL_SECONDS * 4):
                        if self._stop.is_set():
                            break
                        self._wait_if_paused()
                        time.sleep(0.25)
                    continue

                with self._lock:
                    if not self._pause.is_set() and not self._stop.is_set():
                        self.status = "RUNNING"
                    self.message = f"Downloading: {movie['title']}"
                    self.active[movie["id"]] = {
                        "movie_id": movie["id"], "worker_id": worker_id, "title": movie["title"],
                        "stage": "QUEUED", "bytes_downloaded": 0, "total_bytes": None,
                        "speed_bps": 0, "eta_seconds": None, "retries": int(movie.get("retry_count") or 0),
                    }
                events.emit("download_status", self.snapshot())
                try:
                    self._download_one(movie, worker_id)
                finally:
                    with self._lock:
                        self.active.pop(movie["id"], None)
        except Exception as exc:
            with self._lock:
                self.status = "ERROR"
                self.message = str(exc)
            self._stop.set()
            events.emit("error", {"scope": "download", "message": str(exc)})
        finally:
            with self._lock:
                still_running = any(
                    w is not threading.current_thread() and w.is_alive() for w in self._workers
                )
                self.network_wait = False
                if not still_running:
                    self.disk_low = False
                    if self._stop.is_set() and self.status != "ERROR":
                        self.status = "STOPPED"
                        self.message = "Download workers stopped safely."
            events.emit("download_status", self.snapshot())
