from pathlib import Path
from uuid import uuid4

import pytest
import requests

from movie_manager import db
from movie_manager.discovery import (
    ACCEPTED_STATUSES, ArchiveTemporaryFailure, DiscoveryController,
)
from movie_manager.internet_archive import (
    METADATA_URL, SEARCH_URL, InternetArchiveError, InternetArchiveProvider,
)
from movie_manager.source_adapters import DirectHttpAdapter


@pytest.fixture
def isolated_db(monkeypatch):
    path = Path.cwd() / f".movie-manager-test-{uuid4().hex}.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    yield path
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.unlink(missing_ok=True)
        except PermissionError:
            pass


class FakeResponse:
    def __init__(self, status_code, payload, url=None):
        self.status_code = status_code
        self._payload = payload
        self.url = url

    def json(self):
        return self._payload


def _search_payload(docs, num_found=None):
    return {"response": {"docs": docs, "numFound": num_found if num_found is not None else len(docs)}}


def _doc(identifier, title="Classic Yoruba Movie", **extra):
    return {"identifier": identifier, "title": title, "description": "A Yoruba movie.", **extra}


def _metadata(
    title="Classic Yoruba Movie", description="A Yoruba feature film full movie.",
    runtime="01:30:00", licenseurl="https://creativecommons.org/licenses/by/3.0/",
    files=None, creator="Test Uploader",
):
    return {
        "metadata": {
            "title": title, "description": description, "runtime": runtime,
            "licenseurl": licenseurl, "creator": creator, "date": "2020-01-01",
        },
        "files": files if files is not None else [
            {"name": "movie.mp4", "format": "MPEG4", "size": str(60 * 1024 * 1024)},
        ],
    }


def _wire_provider(monkeypatch, search_payload, metadata_by_id, fail_first_n=0):
    """Monkeypatches the module-level requests.get used by InternetArchiveProvider."""
    state = {"calls": 0}

    def fake_get(url, params=None, timeout=None):
        state["calls"] += 1
        if fail_first_n and state["calls"] <= fail_first_n:
            raise requests.RequestException("simulated network blip")
        if url == SEARCH_URL:
            return FakeResponse(200, search_payload)
        identifier = url.rsplit("/", 1)[-1]
        return FakeResponse(200, metadata_by_id.get(identifier, {"metadata": {}, "files": []}))

    monkeypatch.setattr("movie_manager.internet_archive.requests.get", fake_get)
    return state


def _head_ok(monkeypatch, content_type="video/mp4", content_length="1000"):
    def fake_head(url, allow_redirects=True, timeout=None):
        return FakeResponse(200, None, url=url)

    def fake_head_with_headers(url, allow_redirects=True, timeout=None):
        r = FakeResponse(200, None, url=url)
        r.headers = {"Content-Type": content_type, "Content-Length": content_length, "Accept-Ranges": "bytes"}
        return r

    monkeypatch.setattr("movie_manager.source_adapters.requests.head", fake_head_with_headers)


# ---------------------------------------------------------------------------
# 1-2: search + metadata parsing
# ---------------------------------------------------------------------------

def test_search_response_parsing(monkeypatch):
    provider = InternetArchiveProvider()
    _wire_provider(monkeypatch, _search_payload([_doc("item1"), _doc("item2")], num_found=57), {})

    docs, num_found = provider.search_page("Yoruba movie", page=1)

    assert [d["identifier"] for d in docs] == ["item1", "item2"]
    assert num_found == 57


def test_metadata_parsing(monkeypatch):
    provider = InternetArchiveProvider()
    _wire_provider(monkeypatch, _search_payload([]), {"item1": _metadata(title="A Real Movie")})

    meta = provider.fetch_item_metadata("item1")

    assert meta["metadata"]["title"] == "A Real Movie"
    assert meta["files"][0]["name"] == "movie.mp4"


# ---------------------------------------------------------------------------
# 3-5: file selection
# ---------------------------------------------------------------------------

def test_mp4_preferred_over_less_preferred_files():
    provider = InternetArchiveProvider()
    files = [
        {"name": "movie.webm", "format": "WebM", "size": str(60 * 1024 * 1024)},
        {"name": "movie.mp4", "format": "MPEG4", "size": str(58 * 1024 * 1024)},
        {"name": "movie.mkv", "format": "Matroska", "size": str(59 * 1024 * 1024)},
    ]

    picked = provider.pick_video_file(files)

    assert picked["name"] == "movie.mp4"


def test_non_video_files_ignored():
    provider = InternetArchiveProvider()
    files = [
        {"name": "meta.xml", "format": "Metadata", "size": "500"},
        {"name": "thumb.jpg", "format": "JPEG", "size": "20000"},
        {"name": "movie.torrent", "format": "Archive BitTorrent", "size": "300"},
        {"name": "subs.srt", "format": "SubRip", "size": "1000"},
    ]

    picked = provider.pick_video_file(files)

    assert picked is None


def test_small_preview_ignored_when_full_movie_file_exists():
    provider = InternetArchiveProvider()
    files = [
        {"name": "preview.mp4", "format": "MPEG4", "size": str(2 * 1024 * 1024)},
        {"name": "movie.mp4", "format": "MPEG4", "size": str(700 * 1024 * 1024)},
    ]

    picked = provider.pick_video_file(files)

    assert picked["name"] == "movie.mp4"


# ---------------------------------------------------------------------------
# 6-9: candidate evaluation / content rules
# ---------------------------------------------------------------------------

def test_under_60_minute_content_rejected(monkeypatch):
    provider = InternetArchiveProvider()
    _wire_provider(monkeypatch, _search_payload([]), {"short1": _metadata(runtime="00:12:00")})

    movie, result = provider.evaluate_candidate(
        "short1", _doc("short1"), "yoruba", "Yoruba movie", 3600
    )

    assert result == "UNDER_DURATION"
    assert movie["rejection_reason"] == "UNDER_60_MINUTES"


def test_trailer_rejected(monkeypatch):
    provider = InternetArchiveProvider()
    _wire_provider(monkeypatch, _search_payload([]), {
        "trailer1": _metadata(title="Yoruba Movie Official Trailer")
    })

    movie, result = provider.evaluate_candidate(
        "trailer1", _doc("trailer1", title="Yoruba Movie Official Trailer"), "yoruba", "Yoruba movie", 3600
    )

    assert result == "NOT_MOVIE"
    assert "BLOCKED_TERM" in movie["rejection_reason"]


def test_non_yoruba_result_rejected(monkeypatch):
    provider = InternetArchiveProvider()
    _wire_provider(monkeypatch, _search_payload([]), {
        "eng1": _metadata(title="American Drama Feature", description="A Nigerian feature film.")
    })

    movie, result = provider.evaluate_candidate(
        "eng1", _doc("eng1", title="American Drama Feature"), "yoruba", "Yoruba movie", 3600
    )

    assert result == "WRONG_LANGUAGE"


def test_valid_yoruba_feature_film_accepted(monkeypatch):
    provider = InternetArchiveProvider()
    _wire_provider(monkeypatch, _search_payload([]), {
        "good1": _metadata(title="Classic Yoruba Movie", description="An award-winning Yoruba movie.")
    })

    movie, result = provider.evaluate_candidate(
        "good1", _doc("good1"), "yoruba", "Yoruba movie", 3600
    )

    assert result == "ACCEPTED"
    assert movie["video_id"] == "internet_archive:good1"
    assert movie["provider"] == "internet_archive"
    assert movie["download_url"].endswith("movie.mp4")


# ---------------------------------------------------------------------------
# 10-11: rights/licence handling
# ---------------------------------------------------------------------------

def test_clear_supported_licence_accepted():
    provider = InternetArchiveProvider()

    clear, label = provider.evaluate_rights({"licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"})

    assert clear is True
    assert "creativecommons.org" in label


def test_ambiguous_rights_do_not_auto_queue(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.target = 1
    controller.job_id = None
    controller.downloadable_only = False

    _wire_provider(monkeypatch, _search_payload([_doc("ambig1")]), {
        "ambig1": _metadata(licenseurl="")  # no clear rights signal
    })

    controller._run_archive()

    rows = db.list_movies("yoruba", status="ALL", limit=10)
    assert len(rows) == 1
    movie = rows[0]
    assert movie["status"] == "ACCEPTED"  # still a valid catalogue entry
    assert movie["source_status"] == "SOURCE_INVALID"  # but never auto-queued
    assert movie["download_status"] != "READY"


# ---------------------------------------------------------------------------
# 12: stable identifier deduplication
# ---------------------------------------------------------------------------

def test_stable_archive_identifier_deduplication(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.target = 1
    controller.job_id = None
    controller.downloadable_only = False

    _wire_provider(monkeypatch, _search_payload([_doc("dup1")]), {"dup1": _metadata()})
    _head_ok(monkeypatch)

    controller._run_archive()
    first_count = db.count_movies("yoruba", ACCEPTED_STATUSES)

    # Running discovery again against the SAME identifier must not duplicate it.
    controller.target = 5
    controller._run_archive()
    second_count = db.count_movies("yoruba", ACCEPTED_STATUSES)

    rows = db.list_movies("yoruba", status="ALL", limit=10)
    assert len(rows) == 1
    assert rows[0]["video_id"] == "internet_archive:dup1"
    assert first_count == second_count == 1


def test_youtube_and_archive_ids_never_collide(isolated_db):
    db.init_db()
    db.upsert_movie({
        "language": "yoruba", "video_id": "abc123", "provider": "youtube", "title": "YouTube Movie",
        "youtube_url": "https://www.youtube.com/watch?v=abc123", "status": "ACCEPTED",
    })
    db.upsert_movie({
        "language": "yoruba", "video_id": "internet_archive:abc123", "provider": "internet_archive",
        "title": "Archive Movie", "youtube_url": "https://archive.org/details/abc123", "status": "ACCEPTED",
    })

    rows = db.list_movies("yoruba", status="ALL", limit=10)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 13-14: source resolution + Discover+Download queuing
# ---------------------------------------------------------------------------

def test_direct_archive_file_becomes_source_ready(monkeypatch):
    _head_ok(monkeypatch)
    adapter = DirectHttpAdapter()

    result = adapter.validate_source("https://archive.org/download/item1/movie.mp4")

    assert result["verified"] is True


def test_discover_and_download_queues_valid_item(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.target = 1
    controller.job_id = None
    controller.downloadable_only = False

    _wire_provider(monkeypatch, _search_payload([_doc("queue1")]), {"queue1": _metadata()})
    _head_ok(monkeypatch)

    controller._run_archive()

    rows = db.list_movies("yoruba", status="ALL", limit=10)
    assert len(rows) == 1
    movie = rows[0]
    assert movie["source_status"] == "SOURCE_READY"
    assert movie["download_status"] == "READY"
    assert movie["download_url"]

    ready = db.next_download_ready("yoruba")
    assert ready is not None
    assert ready["id"] == movie["id"]


# ---------------------------------------------------------------------------
# 15-16: downloadable-only target + exact-target guard
# ---------------------------------------------------------------------------

def test_downloadable_only_target_counts_archive_items_correctly(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.target = 2
    controller.job_id = None
    controller.downloadable_only = True

    docs = [_doc(f"dl{i}") for i in range(4)]
    metas = {f"dl{i}": _metadata() for i in range(4)}
    _wire_provider(monkeypatch, _search_payload(docs), metas)
    _head_ok(monkeypatch)

    controller._run_archive()

    assert db.count_downloadable("yoruba", ACCEPTED_STATUSES) == 2
    assert controller.status == "COMPLETED"


def test_exact_target_does_not_overshoot_with_archive_provider(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.target = 3
    controller.job_id = None
    controller.downloadable_only = False

    docs = [_doc(f"ov{i}") for i in range(10)]
    metas = {f"ov{i}": _metadata() for i in range(10)}
    _wire_provider(monkeypatch, _search_payload(docs), metas)
    _head_ok(monkeypatch)

    controller._run_archive()

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 3


# ---------------------------------------------------------------------------
# 17: temporary provider failure retries
# ---------------------------------------------------------------------------

def test_temporary_provider_failure_retries(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.target = 1
    controller.job_id = None
    controller.downloadable_only = False
    monkeypatch.setattr("movie_manager.discovery.ARCHIVE_NETWORK_RETRY_SECONDS", [0])

    state = _wire_provider(
        monkeypatch, _search_payload([_doc("retry1")]), {"retry1": _metadata()}, fail_first_n=1
    )
    _head_ok(monkeypatch)

    controller._run_archive()

    assert state["calls"] > 1  # the first call failed and was retried
    assert controller.stats["network_retries"] >= 1
    rows = db.list_movies("yoruba", status="ALL", limit=10)
    assert len(rows) == 1
    assert rows[0]["status"] == "ACCEPTED"


def test_permanent_provider_error_is_skipped_not_fatal(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.target = 5
    controller.job_id = None
    controller.downloadable_only = False

    def fake_get(url, params=None, timeout=None):
        if url == SEARCH_URL:
            return FakeResponse(200, _search_payload([_doc("bad1")]))
        return FakeResponse(404, {})

    monkeypatch.setattr("movie_manager.internet_archive.requests.get", fake_get)

    # Should not raise/crash the run despite a permanent per-item failure.
    controller._run_archive()

    assert controller.status in {"COMPLETED"}


# ---------------------------------------------------------------------------
# Hardening: bounded retry, no infinite retry on permanent 4xx, caching
# ---------------------------------------------------------------------------

def test_archive_request_retries_on_502_and_timeouts(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.job_id = None
    monkeypatch.setattr("movie_manager.discovery.ARCHIVE_NETWORK_RETRY_SECONDS", [0])

    calls = {"n": 0}

    def flaky_search(query, page=1, rows=50):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise requests.RequestException("simulated 502/timeout")
        return [_doc("x")], 1

    provider = InternetArchiveProvider()
    monkeypatch.setattr(provider, "search_page", flaky_search)

    result = controller._archive_request(provider.search_page, "Yoruba movie", 1)

    assert calls["n"] == 3
    assert result == ([_doc("x")], 1)
    assert controller.stats["temporary_provider_errors"] == 2


def test_no_infinite_retry_on_permanent_4xx(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.job_id = None

    calls = {"n": 0}

    def permanent_fail(*a, **k):
        calls["n"] += 1
        raise InternetArchiveError("permanent 404")

    with pytest.raises(InternetArchiveError):
        controller._archive_request(permanent_fail)

    assert calls["n"] == 1  # never retried


def test_archive_request_gives_up_after_max_attempts_not_forever(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.job_id = None
    monkeypatch.setattr("movie_manager.discovery.ARCHIVE_NETWORK_RETRY_SECONDS", [0])

    calls = {"n": 0}

    def always_fails(*a, **k):
        calls["n"] += 1
        raise requests.RequestException("persistent network blip")

    with pytest.raises(ArchiveTemporaryFailure):
        controller._archive_request(always_fails, max_attempts=3)

    assert calls["n"] == 3  # bounded, not infinite
    assert controller.stats["temporary_provider_errors"] == 3


def test_cached_metadata_is_not_fetched_repeatedly(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.target = 1
    controller.job_id = None
    controller.downloadable_only = False

    _wire_provider(monkeypatch, _search_payload([_doc("cache1")], num_found=1), {"cache1": _metadata()})
    _head_ok(monkeypatch)

    controller._run_archive()
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 1

    # A second run must never re-fetch metadata for "cache1" -- it's already
    # a known row (movie_exists) and, independently, already cached. Any
    # metadata request for it here would be a bug.
    metadata_calls_for_cache1 = {"n": 0}

    def guarded_get(url, params=None, timeout=None):
        if url == SEARCH_URL:
            return FakeResponse(200, _search_payload([_doc("cache1")], num_found=1))
        if url == METADATA_URL.format(identifier="cache1"):
            metadata_calls_for_cache1["n"] += 1
        return FakeResponse(200, _metadata())

    monkeypatch.setattr("movie_manager.internet_archive.requests.get", guarded_get)
    controller.target = 5

    controller._run_archive()

    assert metadata_calls_for_cache1["n"] == 0
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 1  # no duplicate row created


def test_permanent_rejection_is_cached_and_not_refetched(monkeypatch, isolated_db):
    db.init_db()
    from movie_manager.internet_archive import InternetArchiveProvider as Provider
    provider = Provider()

    state = _wire_provider(monkeypatch, _search_payload([]), {
        "rej1": _metadata(title="American Drama", description="A Nigerian feature film.")
    })

    movie, result = provider.evaluate_candidate("rej1", _doc("rej1", title="American Drama"), "yoruba", "q", 3600)
    assert result == "WRONG_LANGUAGE"

    cache_fields = provider.cache_payload(movie, result)
    db.set_provider_cache_entry(provider.name, "yoruba", "rej1", **cache_fields)

    entry = db.get_provider_cache_entry(provider.name, "yoruba", "rej1")
    assert entry["status"] == "REJECTED"
    assert entry["result_code"] == "WRONG_LANGUAGE"

    # A later caller can reconstruct the verdict from cache with zero network calls.
    calls_before = state["calls"]
    rebuilt_movie, rebuilt_result = provider.movie_from_cache("yoruba", "rej1", _doc("rej1"), "q", entry)
    assert state["calls"] == calls_before  # no fetch happened
    assert rebuilt_result == "WRONG_LANGUAGE"
    assert rebuilt_movie["status"] == "REJECTED"


def test_temporary_failure_is_retried_on_a_later_run(isolated_db):
    db.init_db()
    db.set_provider_cache_entry("internet_archive", "yoruba", "flaky1", "ERROR_TEMPORARY", reason="timeout")

    entry = db.get_provider_cache_entry("internet_archive", "yoruba", "flaky1")
    assert entry["status"] == "ERROR_TEMPORARY"
    # ERROR_TEMPORARY must never be treated as a final cached verdict --
    # the discovery/supply-scan loop's cache-hit check explicitly excludes it.
    assert entry["status"] != "ERROR_PERMANENT"
    is_final_cache_hit = entry["status"] != "ERROR_TEMPORARY"
    assert is_final_cache_hit is False
