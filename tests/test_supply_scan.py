from pathlib import Path
from uuid import uuid4

import pytest
import requests

from movie_manager import db
from movie_manager.internet_archive import METADATA_URL, SEARCH_URL
from movie_manager.supply_scan import SupplyScanController


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
    def __init__(self, status_code, payload, url=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.url = url
        self.headers = headers or {}

    def json(self):
        return self._payload

    def close(self):
        pass


def _search_payload(docs, num_found=None):
    return {"response": {"docs": docs, "numFound": num_found if num_found is not None else len(docs)}}


def _doc(identifier, title="Classic Yoruba Movie", **extra):
    return {"identifier": identifier, "title": title, "description": "A Yoruba movie.", **extra}


def _metadata(
    title="Classic Yoruba Movie", description="A Yoruba feature film full movie.",
    runtime="01:30:00", licenseurl="https://creativecommons.org/licenses/by/3.0/", files=None,
):
    return {
        "metadata": {
            "title": title, "description": description, "runtime": runtime,
            "licenseurl": licenseurl, "creator": "Test Uploader", "date": "2020-01-01",
        },
        "files": files if files is not None else [
            {"name": "movie.mp4", "format": "MPEG4", "size": str(60 * 1024 * 1024)},
        ],
    }


def _stored_movie(video_id, title=None, status="ACCEPTED", provider="internet_archive"):
    return {
        "language": "yoruba", "video_id": video_id, "provider": provider,
        "title": title or video_id, "normalised_title": (title or video_id).lower(),
        "description": "A Yoruba movie.", "channel_id": None, "channel_title": "Internet Archive",
        "duration_seconds": 5400, "youtube_url": f"https://archive.org/details/{video_id}",
        "status": status, "download_status": "NOT_READY",
    }


def _wire(monkeypatch, docs, metadata_by_id, num_found=None):
    def fake_get(url, params=None, timeout=None):
        if url == SEARCH_URL:
            return FakeResponse(200, _search_payload(docs, num_found=num_found))
        identifier = url.rsplit("/", 1)[-1]
        return FakeResponse(200, metadata_by_id.get(identifier, {"metadata": {}, "files": []}))

    monkeypatch.setattr("movie_manager.internet_archive.requests.get", fake_get)


def _wire_head(monkeypatch, tracker=None):
    """Mocks only requests.head (always 200), which is enough for
    DirectHttpAdapter to verify a source without ever needing its ranged-GET
    fallback -- so this never touches requests.get, avoiding a collision
    with _wire()'s mock of requests.get for archive.org search/metadata
    (they're literally the same module-level attribute)."""
    def fake_head(url, allow_redirects=True, timeout=None):
        if tracker is not None:
            tracker.append(("HEAD", url))
        return FakeResponse(200, None, url=url, headers={
            "Content-Type": "video/mp4", "Content-Length": "1000", "Accept-Ranges": "bytes",
        })

    monkeypatch.setattr("movie_manager.source_adapters.requests.head", fake_head)


def _run_sync(controller, language="yoruba", provider="internet_archive", max_candidates=50):
    controller.start(language, provider, max_candidates)
    controller._thread.join(timeout=15)


# ---------------------------------------------------------------------------
# Supply-scan counters
# ---------------------------------------------------------------------------

def test_supply_scan_counters_cover_every_outcome(monkeypatch, isolated_db):
    db.init_db()
    docs = [
        _doc("good1"),
        _doc("short1"),
        _doc("wronglang1"),
        _doc("trailer1", title="Official Trailer"),
        _doc("novideo1"),
        _doc("ambiguous1"),
    ]
    metas = {
        "good1": _metadata(),
        "short1": _metadata(runtime="00:10:00"),
        "wronglang1": _metadata(title="American Drama", description="A Nigerian feature film."),
        "trailer1": _metadata(title="Official Trailer"),
        "novideo1": _metadata(files=[{"name": "meta.xml", "format": "Metadata", "size": "500"}]),
        "ambiguous1": _metadata(licenseurl=""),
    }
    _wire(monkeypatch, docs, metas, num_found=len(docs))
    _wire_head(monkeypatch)

    controller = SupplyScanController()
    _run_sync(controller, max_candidates=50)

    assert controller.status == "COMPLETED"
    c = controller.counters
    assert c["metadata_checked"] == 6
    assert c["qualifying_60min"] == 2  # good1 + ambiguous1 (still a valid movie)
    assert c["source_ready"] == 1  # only good1 (clear rights + verified URL)
    assert c["under_60_minutes"] == 1
    assert c["wrong_language"] == 1
    assert c["compilation_trailer_etc"] == 1  # trailer1 (BLOCKED_TERM)
    assert c["no_usable_video_file"] == 1
    assert c["ambiguous_rights"] == 1
    assert len(controller.qualifying) == 1
    assert controller.qualifying[0]["identifier"] == "good1"

    # Diagnostic-only: nothing written to the movies table.
    assert db.count_movies("yoruba") == 0


# ---------------------------------------------------------------------------
# No full file download during supply scan
# ---------------------------------------------------------------------------

def test_supply_scan_never_downloads_full_file(monkeypatch, isolated_db):
    db.init_db()
    _wire(monkeypatch, [_doc("good1")], {"good1": _metadata()}, num_found=1)
    calls = []
    _wire_head(monkeypatch, tracker=calls)

    controller = SupplyScanController()
    _run_sync(controller, max_candidates=10)

    assert controller.counters["source_ready"] == 1
    # Source verification used only HEAD requests -- never a streaming/
    # full-body GET of the movie file itself.
    assert calls
    assert all(call[0] == "HEAD" for call in calls)


def test_supply_scan_source_probe_never_uses_unranged_get(monkeypatch, isolated_db):
    """Even when a server doesn't support HEAD, the fallback probe must use
    a byte-ranged GET (Range: bytes=0-0), never a full-body download. Both
    archive.org's JSON API and the direct-file probe share requests.get, so
    this uses one combined fake distinguishing them by call signature."""
    db.init_db()
    ranged_calls = []

    def combined_get(url, params=None, headers=None, stream=None, timeout=None, allow_redirects=True):
        if url == SEARCH_URL:
            return FakeResponse(200, _search_payload([_doc("good1")], num_found=1))
        if url == METADATA_URL.format(identifier="good1"):
            return FakeResponse(200, _metadata())
        ranged_calls.append(headers or {})  # direct-file ranged-GET probe
        return FakeResponse(200, None, url=url, headers={"Content-Type": "video/mp4"})

    def fake_head(url, allow_redirects=True, timeout=None):
        return FakeResponse(405, None, url=url)

    monkeypatch.setattr("movie_manager.internet_archive.requests.get", combined_get)
    monkeypatch.setattr("movie_manager.source_adapters.requests.head", fake_head)

    controller = SupplyScanController()
    _run_sync(controller, max_candidates=10)

    assert controller.counters["source_ready"] == 1
    assert ranged_calls
    assert all("Range" in h for h in ranged_calls)


# ---------------------------------------------------------------------------
# Duplicate provider item protection
# ---------------------------------------------------------------------------

def test_duplicate_identifier_across_query_pages_counted_once(monkeypatch, isolated_db):
    db.init_db()
    # The same identifier appears in results for two different queries.
    _wire(monkeypatch, [_doc("dup1")], {"dup1": _metadata()}, num_found=1)
    _wire_head(monkeypatch)

    controller = SupplyScanController()
    controller.language = "yoruba"
    controller.provider_name = "internet_archive"
    controller.max_candidates = 30
    controller._stop.clear()
    controller._pause.clear()
    controller.counters = controller._fresh_counters()
    controller.qualifying = []

    from movie_manager.language_profiles import PROFILES
    queries = PROFILES["yoruba"]["archive_queries"]
    assert len(queries) >= 2

    controller._run_internet_archive()

    assert controller.counters["metadata_checked"] == 1  # evaluated exactly once
    assert controller.counters["duplicate"] >= 1  # every later sighting counted as duplicate
    assert controller.counters["source_ready"] == 1


# ---------------------------------------------------------------------------
# Exact existing movie filtering remains intact
# ---------------------------------------------------------------------------

def test_supply_scan_skips_identifiers_already_in_movies_table(monkeypatch, isolated_db):
    db.init_db()
    db.upsert_movie(_stored_movie("internet_archive:known1", title="Already Known Movie"))

    metadata_calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        if url == SEARCH_URL:
            return FakeResponse(200, _search_payload([_doc("known1")], num_found=1))
        metadata_calls["n"] += 1
        return FakeResponse(200, _metadata())

    monkeypatch.setattr("movie_manager.internet_archive.requests.get", fake_get)
    _wire_head(monkeypatch)

    controller = SupplyScanController()
    _run_sync(controller, max_candidates=10)

    assert metadata_calls["n"] == 0  # never re-evaluated
    assert controller.counters["duplicate"] >= 1  # every sighting of the known item counts
    assert controller.counters["metadata_checked"] == 0


# ---------------------------------------------------------------------------
# Misc: cache is shared, provider validation, stop is respected
# ---------------------------------------------------------------------------

def test_supply_scan_rejects_unsupported_provider(isolated_db):
    db.init_db()
    controller = SupplyScanController()
    with pytest.raises(ValueError):
        controller.start("yoruba", "youtube", 50)


def test_supply_scan_respects_max_candidates_budget(monkeypatch, isolated_db):
    db.init_db()
    docs = [_doc(f"budget{i}") for i in range(20)]
    metas = {f"budget{i}": _metadata() for i in range(20)}
    _wire(monkeypatch, docs, metas, num_found=20)
    _wire_head(monkeypatch)

    controller = SupplyScanController()
    _run_sync(controller, max_candidates=5)

    assert controller.counters["candidates_searched"] == 5
