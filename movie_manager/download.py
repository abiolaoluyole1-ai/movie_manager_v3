import os
import threading
import time
from pathlib import Path

import requests

from .config import NETWORK_RETRY_SECONDS
from .db import get_setting, next_download_ready, update_download
from .events import events
from .utils import extension_from_url, safe_filename, is_http_url


class DownloadController:
    """Downloader for authorised/direct HTTP(S) movie-file sources."""

    def __init__(self):
        self._thread = None
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.status = "IDLE"
        self.language = "yoruba"
        self.current_movie_id = None
        self.message = ""
        self.network_wait = False

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "language": self.language,
                "current_movie_id": self.current_movie_id,
                "message": self.message,
                "network_wait": self.network_wait,
            }

    def start(self, language="yoruba"):
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self.status == "PAUSED":
                    self._pause.clear()
                    self.status = "RUNNING"
                    events.emit("download_status", self.snapshot())
                return
            self.language = language
            self._stop.clear()
            self._pause.clear()
            self.status = "RUNNING"
            self.message = "Download worker started."
            self.network_wait = False
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
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
            self.message = "Stopping download worker..."
            self.network_wait = False
        events.emit("download_status", self.snapshot())

    def _wait_if_paused(self):
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.25)

    def _output_paths(self, movie):
        root = Path(get_setting("download_root"))
        folder = root / movie["language"].capitalize()
        folder.mkdir(parents=True, exist_ok=True)
        ext = extension_from_url(movie["download_url"])
        filename = safe_filename(movie["title"]) + ext
        final = folder / filename
        if final.exists():
            final = folder / f"{safe_filename(movie['title'])} [{movie['video_id']}]{ext}"
        part = Path(str(final) + ".part")
        return final, part

    def _download_one(self, movie):
        url = movie.get("download_url") or ""
        if not is_http_url(url):
            update_download(movie["id"], download_status="FAILED", download_error="Invalid direct download URL.")
            return

        final, part = self._output_paths(movie)
        attempt = int(movie.get("retry_count") or 0)

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
                                events.emit("download_progress", {
                                    "movie_id": movie["id"], "bytes_downloaded": downloaded,
                                    "total_bytes": total, "speed_bps": speed, "eta_seconds": eta,
                                })
                                last_emit = now

                    actual = part.stat().st_size
                    if total and actual < total:
                        raise requests.RequestException(f"Connection ended early ({actual}/{total} bytes).")

                    os.replace(part, final)
                    update_download(
                        movie["id"], download_status="DOWNLOADED", status="DOWNLOADED",
                        file_path=str(final), bytes_downloaded=actual, total_bytes=actual,
                        download_speed_bps=0, download_eta_seconds=0, download_error=None,
                    )
                    events.emit("download_complete", {"movie_id": movie["id"], "file_path": str(final)})
                    return

            except (requests.RequestException, OSError) as exc:
                delay = NETWORK_RETRY_SECONDS[min(attempt, len(NETWORK_RETRY_SECONDS)-1)]
                attempt += 1
                update_download(
                    movie["id"], download_status="WAITING_NETWORK",
                    download_error=str(exc), retry_count=attempt,
                )
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

    def _run(self):
        try:
            while not self._stop.is_set():
                self._wait_if_paused()
                movie = next_download_ready(self.language)
                if not movie:
                    with self._lock:
                        self.status = "WAITING"
                        self.current_movie_id = None
                        self.message = "Waiting for an authorised local-file download source..."
                    events.emit("download_status", self.snapshot())
                    for _ in range(8):
                        if self._stop.is_set():
                            break
                        self._wait_if_paused()
                        time.sleep(0.25)
                    continue

                with self._lock:
                    self.status = "RUNNING"
                    self.current_movie_id = movie["id"]
                    self.message = f"Downloading: {movie['title']}"
                update_download(movie["id"], download_status="QUEUED", status="QUEUED")
                events.emit("download_status", self.snapshot())
                self._download_one(movie)
        except Exception as exc:
            with self._lock:
                self.status = "ERROR"
                self.message = str(exc)
            events.emit("error", {"scope": "download", "message": str(exc)})
        finally:
            with self._lock:
                self.network_wait = False
                if self._stop.is_set() and self.status != "ERROR":
                    self.status = "STOPPED"
                    self.message = "Download worker stopped safely."
            events.emit("download_status", self.snapshot())
