"""Internet Archive movie provider.

All Internet Archive-specific knowledge (search API, metadata shape, file
selection, rights evaluation) lives here so discovery.py only orchestrates
the generic pause/stop/target-guard/retry loop -- it never needs to know how
Archive search or metadata work.

This provider DISCOVERS movies by itself (unlike source_adapters.py, which
only resolves a download source for a movie that already exists as a
record). A qualifying Archive item is normalised directly into Movie
Manager's existing movie dict shape and given a stable, collision-free
identity of "internet_archive:<identifier>" stored in the existing video_id
column, so the existing UNIQUE(language, video_id) constraint and
movie_exists() dedup logic keep working unchanged for both providers.
"""
import re
from pathlib import Path
from urllib.parse import quote

import requests

from .content_rules import find_blocked_term, find_compilation_phrase, is_yoruba_candidate
from .utils import normalise_title

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"
DETAILS_URL = "https://archive.org/details/{identifier}"
THUMBNAIL_URL = "https://archive.org/services/img/{identifier}"

TEMPORARY_HTTP_STATUSES = {429, 500, 502, 503, 504}
SEARCH_ROWS = 50
DEFAULT_REQUEST_TIMEOUT = 45
# Archive.org has shown itself slower and flakier than YouTube's API in
# practice, so it gets its own (longer, more patient) bounded backoff table
# instead of sharing NETWORK_RETRY_SECONDS with YouTube discovery.
ARCHIVE_NETWORK_RETRY_SECONDS = [5, 10, 20, 40, 60, 90]

# (extension, format-name hints) in preference order: MP4 first, then WebM,
# MKV, and finally M4V/MOV.
VIDEO_FORMAT_PRIORITY = [
    (".mp4", {"mp4", "h.264", "512kb mp4", "h.264 ia"}),
    (".webm", {"webm"}),
    (".mkv", {"matroska", "mkv"}),
    (".m4v", {"m4v"}),
    (".mov", {"quicktime", "mov"}),
]
IGNORED_EXTENSIONS = {
    ".xml", ".txt", ".torrent", ".srt", ".vtt", ".sub", ".sqlite",
    ".jpg", ".jpeg", ".png", ".gif", ".json", ".nfo", ".gz", ".sqlite3",
}
# Below this size, prefer a larger file in the same/best format if one
# exists -- avoids picking a tiny preview/sample over the real movie file.
MIN_MOVIE_FILE_BYTES = 20 * 1024 * 1024

# licenseurl / rights text fragments that count as clear permission to
# download and use. Deliberately conservative: anything not matching one of
# these is treated as ambiguous rather than assumed safe.
CLEAR_LICENSE_HOST_HINTS = ("creativecommons.org", "publicdomain")
CLEAR_RIGHTS_TEXT_HINTS = (
    "public domain", "publicdomain", "creative commons", "cc0", "cc-by", "cc by",
)

_HMS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_MIN_RE = re.compile(r"(\d+)\s*min")
# Same 2-4 digit block repeated 4+ times in a row -- the pattern seen in
# known-spam Archive uploads (e.g. "2435724572457247247247247").
_SPAM_REPEATING_DIGIT_RE = re.compile(r"(\d{2,4})\1{3,}")


def _looks_like_spam_identifier(identifier):
    """Very conservative: only flags identifiers that are ALL digits and
    either unusually long or built from an obviously repeating digit block.
    A word/slug-style identifier (e.g. "BolorunOpani") is never flagged, so
    legitimate older films with unusual-looking identifiers are never
    rejected on this basis alone."""
    if not identifier or not identifier.isdigit():
        return False
    if len(identifier) >= 18:
        return True
    return bool(_SPAM_REPEATING_DIGIT_RE.search(identifier))


def _parse_runtime(value):
    """Best-effort parse of Internet Archive's free-text runtime metadata.
    Returns 0 (unknown) rather than guessing when the format isn't recognised."""
    if not value:
        return 0
    value = str(value).strip()
    match = _HMS_RE.match(value)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        return hours * 3600 + minutes * 60 + seconds
    match = _MIN_RE.search(value)
    if match:
        return int(match.group(1)) * 60
    if value.isdigit():
        return int(value)
    return 0


class InternetArchiveError(RuntimeError):
    """A permanent (non-retryable) Internet Archive request failure."""


class InternetArchiveProvider:
    name = "internet_archive"

    def __init__(self, session=None, request_timeout=DEFAULT_REQUEST_TIMEOUT):
        self._session = session or requests
        self.timeout = request_timeout

    def _get(self, url, params=None):
        """One HTTP GET. Raises requests.RequestException for temporary/
        network-level failures (including HTTP 429/5xx) so the caller's
        existing retry/backoff loop handles them; raises
        InternetArchiveError for permanent failures."""
        response = self._session.get(url, params=params, timeout=self.timeout)
        if response.status_code in TEMPORARY_HTTP_STATUSES:
            raise requests.RequestException(f"Temporary Internet Archive error {response.status_code}")
        if response.status_code >= 400:
            raise InternetArchiveError(f"Internet Archive request failed with HTTP {response.status_code}")
        return response

    def search_page(self, query, page=1, rows=SEARCH_ROWS):
        """Returns (docs: list[dict], num_found: int)."""
        params = {
            "q": f"({query}) AND mediatype:(movies)",
            "fl[]": ["identifier", "title", "description", "year", "licenseurl", "creator", "runtime"],
            "rows": rows,
            "page": page,
            "output": "json",
        }
        response = self._get(SEARCH_URL, params=params)
        payload = response.json()
        resp = payload.get("response", {}) or {}
        return resp.get("docs", []) or [], int(resp.get("numFound", 0) or 0)

    def fetch_item_metadata(self, identifier):
        response = self._get(METADATA_URL.format(identifier=identifier))
        return response.json()

    def pick_video_file(self, files):
        """Chooses the best downloadable movie file from an item's file
        list, preferring MP4 > WebM > MKV > M4V/MOV and a real movie-sized
        file over a tiny preview/sample."""
        candidates = []
        for f in files or []:
            name = f.get("name") or ""
            fmt = (f.get("format") or "").lower()
            ext = Path(name).suffix.lower()
            if not ext or ext in IGNORED_EXTENSIONS:
                continue
            size = int(f.get("size") or 0)
            priority = None
            for rank, (want_ext, fmt_hints) in enumerate(VIDEO_FORMAT_PRIORITY):
                if ext == want_ext or fmt in fmt_hints:
                    priority = rank
                    break
            if priority is None:
                continue
            candidates.append((priority, size, f))

        if not candidates:
            return None

        candidates.sort(key=lambda c: (c[0], -c[1]))
        best_priority = candidates[0][0]
        same_format = [c for c in candidates if c[0] == best_priority]
        full_size = [c for c in same_format if c[1] >= MIN_MOVIE_FILE_BYTES]
        if full_size:
            return full_size[0][2]
        # every same-format candidate is small (or size unknown) -- fall back
        # to any format's largest full-size file before accepting a tiny one
        any_full_size = [c for c in candidates if c[1] >= MIN_MOVIE_FILE_BYTES]
        if any_full_size:
            any_full_size.sort(key=lambda c: (c[0], -c[1]))
            return any_full_size[0][2]
        return same_format[0][2]

    def evaluate_rights(self, meta):
        """Returns (rights_clear: bool, label: str)."""
        licenseurl = (meta.get("licenseurl") or "").strip()
        rights = meta.get("rights") or ""
        if isinstance(rights, list):
            rights = " ".join(str(r) for r in rights)
        rights = str(rights).strip()

        if licenseurl and any(hint in licenseurl.lower() for hint in CLEAR_LICENSE_HOST_HINTS):
            return True, licenseurl
        haystack = f"{licenseurl} {rights}".lower()
        if any(hint in haystack for hint in CLEAR_RIGHTS_TEXT_HINTS):
            return True, (licenseurl or rights)
        return False, (licenseurl or rights or "")

    @staticmethod
    def _extract_duration(meta, files):
        duration = _parse_runtime(meta.get("runtime"))
        if duration:
            return duration
        for f in files or []:
            length = f.get("length")
            if length:
                try:
                    return int(float(length))
                except (TypeError, ValueError):
                    continue
        return 0

    def prescreen_doc(self, doc, language, query):
        """Cheap, network-free rejection using only the search-result doc
        fields already in hand -- no metadata fetch, no HEAD probe. Returns
        (result_code, reason) if the candidate should be rejected outright
        without ever hitting the archive.org metadata API, else None to
        proceed to the (comparatively expensive) metadata fetch.

        Deliberately narrow: only flags an obviously-spam identifier or a
        title/description that already matches a known
        compilation/trailer/clip/etc. phrase. Language is never judged here
        -- the search snippet is too thin a signal, and the fuller
        description in the real metadata is a much more reliable source of
        evidence, so language stays a post-metadata-fetch check.
        """
        identifier = doc.get("identifier") or ""
        if _looks_like_spam_identifier(identifier):
            return "NOT_MOVIE", "SPAM_IDENTIFIER"

        title = str(doc.get("title") or "").strip()
        description = doc.get("description") or ""
        if isinstance(description, list):
            description = " ".join(str(d) for d in description)
        description = str(description)
        lowered = f"{title} {description}".lower()
        title_lowered = title.lower()

        compilation = find_compilation_phrase(title_lowered, description.lower())
        if compilation:
            scope, phrase = compilation
            return "NOT_MOVIE", f"COMPILATION_{scope}:{phrase}"

        blocked = find_blocked_term(lowered)
        if blocked:
            return "NOT_MOVIE", f"BLOCKED_TERM:{blocked}"

        return None

    def _prescreen_movie(self, identifier, doc, language, query, reason):
        """A minimal movie dict for a prescreen-rejected candidate -- enough
        to record/cache the rejection without ever having fetched metadata."""
        title = str(doc.get("title") or identifier).strip()
        description = doc.get("description") or ""
        if isinstance(description, list):
            description = " ".join(str(d) for d in description)
        return {
            "language": language,
            "video_id": f"internet_archive:{identifier}",
            "provider": self.name,
            "title": title or identifier,
            "normalised_title": normalise_title(title),
            "description": str(description)[:5000],
            "channel_id": None,
            "channel_title": "Internet Archive",
            "published_at": None,
            "default_audio_language": None,
            "default_language": None,
            "duration_seconds": 0,
            "thumbnail_url": THUMBNAIL_URL.format(identifier=identifier),
            "youtube_url": DETAILS_URL.format(identifier=identifier),
            "embeddable": False,
            "licence": None,
            "status": "REJECTED",
            "rejection_reason": reason,
            "download_status": "NOT_READY",
            "source_query": query,
        }

    def evaluate_candidate(self, identifier, doc, language, query, min_seconds):
        """Full evaluation pipeline for one search result: cheap prescreen
        first, then (only if it survives) the metadata fetch and full
        evaluation.

        Returns (movie_dict_or_None, result_code). result_code mirrors
        DiscoveryController._evaluate()'s vocabulary (ACCEPTED,
        UNDER_DURATION, WRONG_LANGUAGE, NOT_MOVIE, PROVIDER_ERROR) so the
        shared discovery loop can record stats identically for either
        provider.
        """
        prescreened = self.prescreen_doc(doc, language, query)
        if prescreened:
            result_code, reason = prescreened
            return self._prescreen_movie(identifier, doc, language, query, reason), result_code

        try:
            item = self.fetch_item_metadata(identifier)
        except InternetArchiveError:
            return None, "PROVIDER_ERROR"

        return self.evaluate_metadata(identifier, doc, item, language, query, min_seconds)

    def evaluate_metadata(self, identifier, doc, item, language, query, min_seconds):
        """Evaluates an already-fetched metadata item (see
        fetch_item_metadata). Split out from evaluate_candidate so a
        cache-aware caller (the discovery loop, the supply scan) can reuse
        a previously-cached metadata fetch and skip the network call
        entirely for an identifier it has already seen.
        """
        meta = item.get("metadata", {}) or {}
        files = item.get("files", []) or []

        title = str(meta.get("title") or doc.get("title") or identifier).strip()
        description = meta.get("description") or doc.get("description") or ""
        if isinstance(description, list):
            description = " ".join(str(d) for d in description)
        description = str(description)
        creator = meta.get("creator") or doc.get("creator") or ""
        if isinstance(creator, list):
            creator = ", ".join(str(c) for c in creator)
        channel_title = str(creator)[:255] if creator else "Internet Archive"

        video_id = f"internet_archive:{identifier}"
        movie = {
            "language": language,
            "video_id": video_id,
            "provider": self.name,
            "title": title or identifier,
            "normalised_title": normalise_title(title),
            "description": description[:5000],
            "channel_id": None,
            "channel_title": channel_title,
            "published_at": meta.get("date") or meta.get("publicdate"),
            "default_audio_language": None,
            "default_language": None,
            "duration_seconds": self._extract_duration(meta, files),
            "thumbnail_url": THUMBNAIL_URL.format(identifier=identifier),
            "youtube_url": DETAILS_URL.format(identifier=identifier),
            "embeddable": False,
            "licence": meta.get("licenseurl") or meta.get("rights"),
            "status": "DISCOVERED",
            "download_status": "NOT_READY",
            "source_query": query,
        }

        duration = movie["duration_seconds"]
        if duration and duration < min_seconds:
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = "UNDER_60_MINUTES"
            return movie, "UNDER_DURATION"

        lowered = f"{title} {description}".lower()
        title_lowered = title.lower()

        if language == "yoruba" and not is_yoruba_candidate(
            None, None, title, description, channel_title, query
        ):
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = "WRONG_LANGUAGE"
            return movie, "WRONG_LANGUAGE"

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

        video_file = self.pick_video_file(files)
        if video_file is None:
            movie["status"] = "REJECTED"
            movie["rejection_reason"] = "NO_VIDEO_FILE"
            return movie, "NOT_MOVIE"

        filename = video_file.get("name")
        movie["download_url"] = DOWNLOAD_URL.format(identifier=identifier, filename=quote(filename))
        movie["_archive_filename"] = filename
        movie["_archive_file_size"] = int(video_file.get("size") or 0)

        rights_clear, rights_label = self.evaluate_rights(meta)
        movie["licence"] = rights_label or movie["licence"]
        movie["_rights_clear"] = rights_clear

        movie["status"] = "ACCEPTED"
        return movie, "ACCEPTED"

    def movie_from_cache(self, language, identifier, doc, query, cache_entry):
        """Reconstructs a movie dict + result code from a previously cached
        verdict for this identifier -- no metadata fetch, no HEAD probe.
        """
        status = cache_entry["status"]
        title = cache_entry.get("title") or doc.get("title") or identifier
        movie = {
            "language": language,
            "video_id": f"internet_archive:{identifier}",
            "provider": self.name,
            "title": title,
            "normalised_title": normalise_title(title),
            "description": str(doc.get("description") or "")[:5000],
            "channel_id": None,
            "channel_title": "Internet Archive",
            "published_at": None,
            "default_audio_language": None,
            "default_language": None,
            "duration_seconds": cache_entry.get("duration_seconds") or 0,
            "thumbnail_url": THUMBNAIL_URL.format(identifier=identifier),
            "youtube_url": DETAILS_URL.format(identifier=identifier),
            "embeddable": False,
            "licence": cache_entry.get("licence"),
            "status": "DISCOVERED",
            "download_status": "NOT_READY",
            "source_query": query,
        }
        if status in ("ACCEPTED_READY", "ACCEPTED_AMBIGUOUS"):
            movie["status"] = "ACCEPTED"
            movie["download_url"] = cache_entry.get("download_url")
            movie["_rights_clear"] = status == "ACCEPTED_READY"
            return movie, "ACCEPTED"
        movie["status"] = "REJECTED"
        movie["rejection_reason"] = cache_entry.get("reason")
        return movie, cache_entry.get("result_code") or "NOT_MOVIE"

    @staticmethod
    def cache_payload(movie, result):
        """Builds the set_provider_cache_entry(**payload) fields for a
        freshly-evaluated candidate's result. Returns None if there's
        nothing worth caching (e.g. movie is None)."""
        if movie is None:
            return None
        if result == "ACCEPTED":
            status = "ACCEPTED_READY" if movie.get("_rights_clear") else "ACCEPTED_AMBIGUOUS"
        else:
            status = "REJECTED"
        return {
            "status": status, "result_code": result, "reason": movie.get("rejection_reason"),
            "title": movie.get("title"), "duration_seconds": movie.get("duration_seconds"),
            "licence": movie.get("licence"), "rights_clear": movie.get("_rights_clear"),
            "download_url": movie.get("download_url"),
        }


provider = InternetArchiveProvider()
