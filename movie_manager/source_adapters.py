"""Automatic download-source resolution.

Adapters resolve an authorised, direct HTTP(S) local-file source for a
movie. They never touch youtube.com/watch or youtu.be URLs as a media
source -- those remain metadata/playback only, handled elsewhere by the
existing YouTube embed player.
"""
from pathlib import Path
from urllib.parse import urlparse

import requests

from .source_mappings import load_mappings
from .utils import is_direct_http_candidate, safe_filename

ALLOWED_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
PROBE_TIMEOUT = 12


def _url_extension(url):
    try:
        return Path(urlparse(url).path).suffix.lower()
    except Exception:
        return ""


class DownloadSourceAdapter:
    name = "base"

    def can_handle(self, movie):
        raise NotImplementedError

    def resolve(self, movie):
        raise NotImplementedError

    def validate_source(self, url):
        raise NotImplementedError


class DirectHttpAdapter(DownloadSourceAdapter):
    """Validates/probes a genuine direct HTTP(S) movie-file URL.

    Used both for a manually pasted "Advanced / Manual source" URL, and by
    other adapters (e.g. the local mapping adapter) to verify a candidate
    URL before it is trusted.
    """

    name = "direct_http"

    def can_handle(self, movie):
        return bool((movie.get("download_url") or "").strip())

    def resolve(self, movie):
        return self.validate_source((movie.get("download_url") or "").strip())

    def validate_source(self, url):
        result = {
            "provider": self.name, "download_url": url, "filename": None,
            "mime_type": None, "content_length": None, "supports_resume": False,
            "verified": False, "error": None,
        }
        if not is_direct_http_candidate(url):
            result["error"] = "Not an authorised direct HTTP(S) movie-file URL."
            return result

        try:
            response = requests.head(url, allow_redirects=True, timeout=PROBE_TIMEOUT)
            if response.status_code >= 400 or response.status_code in (405, 501):
                response = requests.get(
                    url, headers={"Range": "bytes=0-0"}, stream=True,
                    timeout=PROBE_TIMEOUT, allow_redirects=True,
                )
                response.close()
        except requests.RequestException as exc:
            result["error"] = f"Could not reach source: {exc}"
            return result

        final_url = response.url or url
        if not is_direct_http_candidate(final_url):
            result["error"] = "Redirect target is not an authorised direct HTTP(S) source."
            return result
        if response.status_code >= 400:
            result["error"] = f"Source returned HTTP {response.status_code}."
            return result

        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        content_length = response.headers.get("Content-Length")
        accept_ranges = (response.headers.get("Accept-Ranges") or "").lower()

        ext = _url_extension(final_url)
        if ext and ext not in ALLOWED_EXTENSIONS:
            result["error"] = f"Unsupported file type: {ext}"
            return result
        if content_type == "text/html":
            result["error"] = "Source returned a webpage, not a media file."
            return result

        result.update({
            "download_url": final_url,
            "filename": safe_filename(Path(urlparse(final_url).path).name or "movie"),
            "mime_type": content_type or None,
            "content_length": int(content_length) if content_length and content_length.isdigit() else None,
            "supports_resume": accept_ranges == "bytes" or response.status_code == 206,
            "verified": True,
        })
        return result


class LocalMappingAdapter(DownloadSourceAdapter):
    """Looks up an authorised source URL from data/download_sources.json,
    matched by YouTube video_id, then validates it via DirectHttpAdapter."""

    name = "local_mapping"

    def __init__(self, http_adapter=None):
        self._http = http_adapter or DirectHttpAdapter()

    def can_handle(self, movie):
        mapping = load_mappings()
        return bool(movie.get("video_id")) and movie["video_id"] in mapping

    def resolve(self, movie):
        mapping = load_mappings()
        entry = mapping.get(movie.get("video_id"))
        if not entry:
            return None
        result = self._http.validate_source((entry.get("download_url") or "").strip())
        result["provider"] = self.name
        return result

    def validate_source(self, url):
        return self._http.validate_source(url)


class SourceResolver:
    """Tries each adapter in order; the first verified result wins. A
    candidate that fails validation still lets later adapters try, but if
    nothing verifies, the last invalid candidate's error is reported."""

    def __init__(self, adapters=None):
        self.adapters = adapters if adapters is not None else [
            DirectHttpAdapter(), LocalMappingAdapter(),
        ]

    def resolve_movie(self, movie):
        attempted = None
        for adapter in self.adapters:
            try:
                if not adapter.can_handle(movie):
                    continue
                result = adapter.resolve(movie)
            except Exception as exc:
                result = {"verified": False, "error": str(exc), "provider": adapter.name}
            if not result:
                continue
            result.setdefault("provider", adapter.name)
            if result.get("verified"):
                return {
                    "source_status": "SOURCE_READY",
                    "provider": result.get("provider"),
                    "error": None,
                    "download_url": result.get("download_url"),
                    "filename": result.get("filename"),
                    "mime_type": result.get("mime_type"),
                    "content_length": result.get("content_length"),
                    "supports_resume": result.get("supports_resume"),
                }
            attempted = result

        if attempted is not None:
            return {
                "source_status": "SOURCE_INVALID", "provider": attempted.get("provider"),
                "error": attempted.get("error"), "download_url": None,
            }
        return {"source_status": "SOURCE_MISSING", "provider": None, "error": None, "download_url": None}


resolver = SourceResolver()
