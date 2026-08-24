import json

import pytest

from movie_manager import db, source_mappings
from movie_manager.discovery import ACCEPTED_STATUSES, DiscoveryController
from movie_manager.runtime import Runtime
from movie_manager.source_adapters import DirectHttpAdapter, LocalMappingAdapter, SourceResolver
from movie_manager.webapp import create_app


@pytest.fixture
def isolated_mapping(monkeypatch, tmp_path):
    mapping_path = tmp_path / "download_sources.json"
    monkeypatch.setattr(source_mappings, "MAPPING_PATH", mapping_path)
    return mapping_path


class FakeHeadResponse:
    def __init__(self, status_code, headers=None, url=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url

    def close(self):
        pass


class FakeSearchResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _movie(video_id="v1", language="yoruba", download_url=""):
    return {"id": 1, "language": language, "video_id": video_id, "title": "Test Movie", "download_url": download_url}


def _stored_movie(video_id, title=None, status="ACCEPTED", language="yoruba"):
    return {
        "language": language, "video_id": video_id, "title": title or f"Movie {video_id}",
        "normalised_title": (title or video_id).lower(), "description": "A Yoruba movie.",
        "channel_id": "channel", "channel_title": "Yoruba Channel", "duration_seconds": 4200,
        "youtube_url": f"https://example.test/{video_id}", "status": status, "download_status": "NOT_READY",
    }


def _fake_item(video_id):
    return {
        "id": video_id,
        "snippet": {
            "title": f"Classic Yoruba Movie {video_id}",
            "description": "An award-winning Yoruba movie full length feature.",
            "channelTitle": "Yoruba Channel",
            "thumbnails": {},
        },
        "contentDetails": {"duration": "PT1H30M"},
        "status": {"embeddable": True},
    }


def _wire_fake_network(monkeypatch, controller, video_ids):
    """One search page returning exactly these video ids, then exhausted."""
    def fake_search(params):
        items = [{"id": {"videoId": vid}} for vid in video_ids]
        return FakeSearchResponse(200, {"items": items, "nextPageToken": None})

    def fake_fetch_details(ids):
        return [_fake_item(vid) for vid in ids]

    monkeypatch.setattr(controller, "_search", fake_search)
    monkeypatch.setattr(controller, "_fetch_details", fake_fetch_details)
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *a, **k: None)
    monkeypatch.setattr("os.getenv", lambda key, default="": "fake-key" if key == "YOUTUBE_API_KEY" else default)


def _mock_head_ok(monkeypatch, content_type="video/mp4", content_length="1000", accept_ranges="bytes"):
    def fake_head(url, allow_redirects=True, timeout=None):
        return FakeHeadResponse(200, headers={
            "Content-Type": content_type, "Content-Length": content_length, "Accept-Ranges": accept_ranges,
        }, url=url)
    monkeypatch.setattr("movie_manager.source_adapters.requests.head", fake_head)


# ---------------------------------------------------------------------------
# 1-4: DirectHttpAdapter validation
# ---------------------------------------------------------------------------

def test_valid_direct_mp4_source_is_source_ready(monkeypatch):
    _mock_head_ok(monkeypatch)
    adapter = DirectHttpAdapter()

    result = adapter.validate_source("https://example.test/movie.mp4")

    assert result["verified"] is True
    assert result["content_length"] == 1000
    assert result["supports_resume"] is True

    resolution = SourceResolver(adapters=[adapter]).resolve_movie(
        _movie(download_url="https://example.test/movie.mp4")
    )
    assert resolution["source_status"] == "SOURCE_READY"
    assert resolution["download_url"] == "https://example.test/movie.mp4"


def test_bad_url_is_source_invalid():
    resolver = SourceResolver(adapters=[DirectHttpAdapter()])

    resolution = resolver.resolve_movie(_movie(download_url="not-a-url"))

    assert resolution["source_status"] == "SOURCE_INVALID"
    assert resolution["error"]


def test_404_is_source_invalid(monkeypatch):
    monkeypatch.setattr(
        "movie_manager.source_adapters.requests.head",
        lambda *a, **k: FakeHeadResponse(404, url="https://example.test/missing.mp4")
    )
    monkeypatch.setattr(
        "movie_manager.source_adapters.requests.get",
        lambda *a, **k: FakeHeadResponse(404, url="https://example.test/missing.mp4")
    )
    resolver = SourceResolver(adapters=[DirectHttpAdapter()])

    resolution = resolver.resolve_movie(_movie(download_url="https://example.test/missing.mp4"))

    assert resolution["source_status"] == "SOURCE_INVALID"
    assert "404" in resolution["error"]


def test_youtube_watch_url_is_rejected_as_direct_source():
    adapter = DirectHttpAdapter()

    watch = adapter.validate_source("https://www.youtube.com/watch?v=abc123")
    short = adapter.validate_source("https://youtu.be/abc123")

    assert watch["verified"] is False
    assert short["verified"] is False

    resolver = SourceResolver(adapters=[DirectHttpAdapter()])
    resolution = resolver.resolve_movie(_movie(download_url="https://www.youtube.com/watch?v=abc123"))
    assert resolution["source_status"] == "SOURCE_INVALID"


# ---------------------------------------------------------------------------
# 5-6: local mapping adapter
# ---------------------------------------------------------------------------

def test_mapping_by_video_id_resolves(monkeypatch, isolated_mapping):
    source_mappings.save_mappings({"v1": {"download_url": "https://example.test/movie.mp4"}})
    _mock_head_ok(monkeypatch)

    resolution = SourceResolver().resolve_movie(_movie(video_id="v1", download_url=""))

    assert resolution["source_status"] == "SOURCE_READY"
    assert resolution["provider"] == "local_mapping"


def test_no_mapping_is_source_missing(isolated_mapping):
    resolution = SourceResolver().resolve_movie(_movie(video_id="unmapped", download_url=""))

    assert resolution["source_status"] == "SOURCE_MISSING"


# ---------------------------------------------------------------------------
# 7: bulk resolver endpoint
# ---------------------------------------------------------------------------

def test_bulk_resolver_endpoint_works(monkeypatch, isolated_db, isolated_mapping):
    db.init_db()
    db.upsert_movie(_stored_movie("v1"))
    db.upsert_movie(_stored_movie("v2"))
    source_mappings.save_mappings({"v1": {"download_url": "https://example.test/a.mp4"}})
    _mock_head_ok(monkeypatch)
    monkeypatch.setattr("movie_manager.webapp.runtime", Runtime())
    client = create_app().test_client()

    r = client.post("/api/movies/bulk/resolve-sources", json={"language": "yoruba"}).get_json()

    assert r["ok"] is True
    assert r["checked"] == 2
    assert r["ready"] == 1
    assert r["missing"] == 1
    assert r["errors"] == 0


def test_bulk_resolver_endpoint_can_target_selected_ids(monkeypatch, isolated_db, isolated_mapping):
    db.init_db()
    db.upsert_movie(_stored_movie("v1"))
    db.upsert_movie(_stored_movie("v2"))
    source_mappings.save_mappings({
        "v1": {"download_url": "https://example.test/a.mp4"},
        "v2": {"download_url": "https://example.test/b.mp4"},
    })
    _mock_head_ok(monkeypatch)
    monkeypatch.setattr("movie_manager.webapp.runtime", Runtime())
    client = create_app().test_client()
    only_v1 = db.get_movie_id("yoruba", "v1")

    r = client.post(
        "/api/movies/bulk/resolve-sources", json={"language": "yoruba", "ids": [only_v1]}
    ).get_json()

    assert r["checked"] == 1
    assert r["ready"] == 1
    v2 = db.get_movie(db.get_movie_id("yoruba", "v2"))
    assert v2["source_status"] == "SOURCE_PENDING"


# ---------------------------------------------------------------------------
# 8-10: CSV/JSON import and duplicate handling
# ---------------------------------------------------------------------------

def test_duplicate_mappings_in_import_handled_safely(isolated_mapping):
    entries = [
        ("v1", "https://example.test/a.mp4"),
        ("v1", "https://example.test/a.mp4"),
        ("v1", "https://example.test/should-not-apply.mp4"),
    ]

    result = source_mappings.import_mapping_entries(entries)

    assert result["imported"] == 1
    assert result["duplicates"] == 2
    mapping = source_mappings.load_mappings()
    assert mapping["v1"]["download_url"] == "https://example.test/a.mp4"


def test_csv_import_works(isolated_mapping):
    csv_text = "video_id,download_url\nv1,https://example.test/a.mp4\nv2,https://example.test/b.mp4\n"

    entries = source_mappings.parse_csv_mapping(csv_text)
    result = source_mappings.import_mapping_entries(entries)

    assert result["imported"] == 2
    assert result["invalid"] == 0
    mapping = source_mappings.load_mappings()
    assert mapping["v2"]["download_url"] == "https://example.test/b.mp4"


def test_json_import_works(isolated_mapping):
    json_text = json.dumps({
        "v1": {"download_url": "https://example.test/a.mp4"},
        "v2": "https://example.test/b.mp4",
    })

    entries = source_mappings.parse_json_mapping(json_text)
    result = source_mappings.import_mapping_entries(entries)

    assert result["imported"] == 2
    mapping = source_mappings.load_mappings()
    assert mapping["v1"]["download_url"] == "https://example.test/a.mp4"
    assert mapping["v2"]["download_url"] == "https://example.test/b.mp4"


def test_import_endpoint_reports_unknown_video_ids(isolated_db, isolated_mapping):
    db.init_db()
    db.upsert_movie(_stored_movie("known1"))
    monkeypatch_entries = [("known1", "https://example.test/a.mp4"), ("stranger", "https://example.test/b.mp4")]

    result = source_mappings.import_mapping_entries(monkeypatch_entries, known_video_ids=db.all_video_ids())

    assert result["imported"] == 2
    assert result["unknown_video_ids"] == 1


def test_import_rejects_invalid_urls(isolated_mapping):
    entries = [("v1", "not-http"), ("v2", "https://www.youtube.com/watch?v=abc")]

    result = source_mappings.import_mapping_entries(entries)

    assert result["invalid"] == 2
    assert result["imported"] == 0


# ---------------------------------------------------------------------------
# 11-12: discovery integration
# ---------------------------------------------------------------------------

def test_accepted_youtube_movie_is_immediately_ready_for_download(monkeypatch, isolated_db):
    """_accept_and_resolve is the single accept+resolve chokepoint every
    provider's search cycle goes through, and for the YouTube-only active
    product it always marks a freshly accepted movie SOURCE_READY/READY via
    YTDLP immediately -- no CSV/JSON source mapping is needed or consulted
    at accept time (that manual-override path is a separate, later step)."""
    db.init_db()
    controller = DiscoveryController()
    _wire_fake_network(monkeypatch, controller, ["item0"])
    controller.language = "yoruba"
    controller.target = 1
    controller.job_id = None

    controller._run()

    rows = db.list_movies("yoruba", status="ALL", limit=10)
    assert len(rows) == 1
    movie = rows[0]
    assert movie["status"] == "ACCEPTED"
    assert movie["source_status"] == "SOURCE_READY"
    assert movie["download_status"] == "READY"
    assert movie["provider"] == "youtube"
    assert movie["download_backend"] == "YTDLP"

    ready = db.next_download_ready("yoruba")
    assert ready is not None
    assert ready["id"] == movie["id"]


# Retired: test_source_missing_does_not_block_discovery asserted that a
# freshly discovered YouTube movie without a CSV/JSON source mapping lands
# as SOURCE_MISSING/NOT_READY. That's no longer possible for the active
# YouTube-only product -- see test_accepted_youtube_movie_is_immediately_
# ready_for_download above, which now covers the real (always-SOURCE_READY)
# behavior for the same discovery path.


# ---------------------------------------------------------------------------
# 13-14: downloadable-only target
# ---------------------------------------------------------------------------

def test_downloadable_only_target_counts_only_ready_or_downloaded(isolated_db):
    db.init_db()
    db.upsert_movie(_stored_movie("v1"))
    db.upsert_movie(_stored_movie("v2"))
    ready_id = db.get_movie_id("yoruba", "v1")

    db.apply_source_resolution(
        ready_id, "yoruba",
        {"source_status": "SOURCE_READY", "provider": "test", "download_url": "https://example.test/a.mp4"},
        ACCEPTED_STATUSES,
    )

    assert db.count_downloadable("yoruba", ACCEPTED_STATUSES) == 1
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 2


def test_start_reads_downloadable_only_flag_from_settings(monkeypatch, isolated_db):
    db.init_db()
    db.set_setting("count_only_downloadable", "1")
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.count_movies", lambda *a, **k: 3)
    monkeypatch.setattr("movie_manager.discovery.count_downloadable", lambda *a, **k: 3)
    monkeypatch.setattr("movie_manager.discovery.create_job", lambda *a: 1)
    monkeypatch.setattr(
        "movie_manager.discovery.threading.Thread",
        lambda **kwargs: type("T", (), {"start": lambda self: None, "is_alive": lambda self: False})()
    )

    controller.start("yoruba", 10)

    assert controller.downloadable_only is True


def test_downloadable_only_mode_stops_exactly_at_target_without_overshoot(monkeypatch, isolated_db, isolated_mapping):
    db.init_db()
    db.set_setting("count_only_downloadable", "1")
    mapping = {f"item{i}": {"download_url": f"https://example.test/{i}.mp4"} for i in range(0, 20, 2)}
    source_mappings.save_mappings(mapping)
    _mock_head_ok(monkeypatch)

    controller = DiscoveryController()
    _wire_fake_network(monkeypatch, controller, [f"item{i}" for i in range(20)])
    controller.language = "yoruba"
    controller.target = 5
    controller.job_id = None
    controller.downloadable_only = True

    controller._run()

    assert db.count_downloadable("yoruba", ACCEPTED_STATUSES) == 5
    assert controller.status == "COMPLETED"
    # Discovery had to scan well past 5 raw accepts to find 5 downloadable ones,
    # but the downloadable count itself never overshoots the target.
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) >= 5
