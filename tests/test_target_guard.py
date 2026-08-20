import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from movie_manager import db
from movie_manager.discovery import (
    ACCEPTED_STATUSES, DiscoveryController, SEARCH_URL, VIDEOS_URL,
)
from movie_manager.runtime import Runtime
from movie_manager.webapp import create_app


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


@pytest.fixture
def app_runtime(monkeypatch, isolated_db):
    db.init_db()
    runtime = Runtime()
    monkeypatch.setattr("movie_manager.webapp.runtime", runtime)
    client = create_app().test_client()
    return client, runtime


def _seeded_movie(video_id, language="yoruba", status="ACCEPTED"):
    return {
        "language": language, "video_id": video_id, "title": f"Seed {video_id}",
        "normalised_title": f"seed {video_id}", "description": "A Yoruba movie.",
        "channel_id": "channel", "channel_title": "Yoruba Channel",
        "duration_seconds": 4200, "youtube_url": f"https://example.test/{video_id}",
        "status": status, "download_status": "NOT_READY",
    }


def _seed_accepted(n, language="yoruba", prefix="seed"):
    for i in range(n):
        db.upsert_movie(_seeded_movie(f"{prefix}{i}", language=language))


def _fake_item(video_id, title=None):
    return {
        "id": video_id,
        "snippet": {
            "title": title or f"Classic Yoruba Movie {video_id}",
            "description": "An award-winning Yoruba movie full length feature.",
            "channelTitle": "Yoruba Channel",
            "thumbnails": {},
        },
        "contentDetails": {"duration": "PT1H30M"},
        "status": {"embeddable": True},
    }


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _wire_unlimited_fake_network(monkeypatch, controller, batch_size=50):
    """Every query page returns a fresh batch of unique, ACCEPTED-worthy candidates."""
    call_count = {"n": 0}

    def fake_search(params):
        call_count["n"] += 1
        q = params["q"].replace(" ", "_")
        items = [
            {"id": {"videoId": f"{q}_{call_count['n']}_{i}"}}
            for i in range(batch_size)
        ]
        return FakeResponse(200, {"items": items, "nextPageToken": None})

    def fake_fetch_details(ids):
        return [_fake_item(vid) for vid in ids]

    monkeypatch.setattr(controller, "_search", fake_search)
    monkeypatch.setattr(controller, "_fetch_details", fake_fetch_details)
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *a, **k: None)
    monkeypatch.setattr("os.getenv", lambda key, default="": "fake-key" if key == "YOUTUBE_API_KEY" else default)
    return call_count


def _run_controller(controller, language, target):
    controller.language = language
    controller.target = target
    controller.job_id = None
    controller._run()


# ---------------------------------------------------------------------------
# 1-3: basic exact-target discovery from various starting points
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("start_accepted,target", [(0, 10), (9, 10), (10, 30)])
def test_discovery_stops_at_exact_target(monkeypatch, isolated_db, start_accepted, target):
    db.init_db()
    _seed_accepted(start_accepted)
    controller = DiscoveryController()
    _wire_unlimited_fake_network(monkeypatch, controller)

    _run_controller(controller, "yoruba", target)

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == target
    assert controller.status == "COMPLETED"


# ---------------------------------------------------------------------------
# 4: a single 50-item batch cannot overshoot target
# ---------------------------------------------------------------------------

def test_single_batch_of_fifty_candidates_cannot_overshoot_target(monkeypatch, isolated_db):
    db.init_db()
    _seed_accepted(29)
    controller = DiscoveryController()
    controller.language = "yoruba"

    with monkeypatch.context() as m:
        m.setattr(
            "movie_manager.discovery.find_probable_title_duplicate", lambda *a, **k: None
        )
        for i in range(50):
            movie, result = controller._evaluate(
                _fake_item(f"batch{i}"), "Yoruba full movie", 3600
            )
            assert result == "ACCEPTED"
            stored = db.upsert_movie_with_target_guard(movie, ACCEPTED_STATUSES, 30)
            if db.count_movies("yoruba", ACCEPTED_STATUSES) >= 30:
                break

    final = db.count_movies("yoruba", ACCEPTED_STATUSES)
    assert final == 30


# ---------------------------------------------------------------------------
# 5-7: replacement flows (single reject, bulk reject, wrong-language) land exactly on target
# ---------------------------------------------------------------------------

def test_single_remove_and_replace_returns_exactly_to_target(monkeypatch, app_runtime):
    client, runtime = app_runtime
    db.set_setting("maintain_target", "1")
    db.set_setting("target:yoruba", "30")
    _seed_accepted(30)
    ids = [row["id"] for row in client.get("/api/movies?language=yoruba&status=ALL").get_json()]
    _wire_unlimited_fake_network(monkeypatch, runtime.discovery)

    r = client.post(f"/api/movies/{ids[0]}/reject", json={"reason": "USER_REJECTED"})
    assert r.get_json()["ok"] is True
    runtime.discovery._thread.join(timeout=5)

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 30


def test_bulk_remove_five_returns_exactly_to_target(monkeypatch, app_runtime):
    client, runtime = app_runtime
    db.set_setting("maintain_target", "1")
    db.set_setting("target:yoruba", "30")
    _seed_accepted(30)
    ids = [row["id"] for row in client.get("/api/movies?language=yoruba&status=ALL").get_json()]
    _wire_unlimited_fake_network(monkeypatch, runtime.discovery)

    r = client.post("/api/movies/bulk/reject", json={"ids": ids[:5], "language": "yoruba", "reason": "USER_REJECTED"})
    body = r.get_json()
    assert body["updated"] == 5
    assert body["replacement_triggered"] is True
    runtime.discovery._thread.join(timeout=5)

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 30


def test_wrong_language_replacement_returns_exactly_to_target(monkeypatch, app_runtime):
    client, runtime = app_runtime
    db.set_setting("maintain_target", "1")
    db.set_setting("target:yoruba", "30")
    _seed_accepted(30)
    ids = [row["id"] for row in client.get("/api/movies?language=yoruba&status=ALL").get_json()]
    _wire_unlimited_fake_network(monkeypatch, runtime.discovery)

    r = client.post("/api/movies/bulk/reject", json={"ids": ids[:3], "language": "yoruba", "reason": "WRONG_LANGUAGE"})
    assert r.get_json()["updated"] == 3
    runtime.discovery._thread.join(timeout=5)

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 30
    rejected = client.get("/api/movies?language=yoruba&status=REJECTED").get_json()
    assert all(row["rejection_reason"] == "WRONG_LANGUAGE" for row in rejected)


# ---------------------------------------------------------------------------
# 8: Continue after raising the target stops exactly at the new target
# ---------------------------------------------------------------------------

def test_continue_after_raising_target_stops_exactly_at_new_target(monkeypatch, isolated_db):
    db.init_db()
    _seed_accepted(30)
    controller = DiscoveryController()
    _wire_unlimited_fake_network(monkeypatch, controller)

    _run_controller(controller, "yoruba", 100)

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 100
    assert controller.status == "COMPLETED"


# ---------------------------------------------------------------------------
# 9: pause/resume cycling during a run never overshoots
# ---------------------------------------------------------------------------

def test_resume_after_pause_does_not_overshoot(monkeypatch, isolated_db):
    db.init_db()
    _seed_accepted(8)
    controller = DiscoveryController()
    _wire_unlimited_fake_network(monkeypatch, controller, batch_size=5)

    controller.start("yoruba", 10)
    time.sleep(0.02)
    controller.pause()
    time.sleep(0.05)
    controller.resume()
    controller._thread.join(timeout=5)

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 10


# ---------------------------------------------------------------------------
# 10: a network retry does not cause duplicate/extra acceptance
# ---------------------------------------------------------------------------

def test_network_retry_does_not_cause_duplicate_or_extra_acceptance(monkeypatch, isolated_db):
    db.init_db()
    _seed_accepted(8)
    controller = DiscoveryController()
    controller.language = "yoruba"

    call_state = {"n": 0}

    def flaky_get(url, params, timeout):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise requests.RequestException("simulated network blip")
        if url == SEARCH_URL:
            items = [{"id": {"videoId": f"net{i}"}} for i in range(5)]
            return FakeResponse(200, {"items": items, "nextPageToken": None})
        if url == VIDEOS_URL:
            ids = params["id"].split(",")
            return FakeResponse(200, {"items": [_fake_item(vid) for vid in ids]})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("movie_manager.discovery.requests.get", flaky_get)
    monkeypatch.setattr("movie_manager.discovery.NETWORK_RETRY_SECONDS", [0])
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *a, **k: None)
    monkeypatch.setattr("os.getenv", lambda key, default="": "fake-key" if key == "YOUTUBE_API_KEY" else default)

    _run_controller(controller, "yoruba", 10)

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 10
    rows = db.list_movies("yoruba", status="ALL", limit=200)
    video_ids = [row["video_id"] for row in rows]
    assert len(video_ids) == len(set(video_ids))


# ---------------------------------------------------------------------------
# 11: duplicate discovery workers remain prevented
# ---------------------------------------------------------------------------

def test_duplicate_discovery_workers_remain_prevented(monkeypatch, isolated_db):
    db.init_db()
    controller = DiscoveryController()
    _wire_unlimited_fake_network(monkeypatch, controller, batch_size=1)

    controller.start("yoruba", 5)
    first_thread = controller._thread

    with pytest.raises(RuntimeError):
        controller.start("yoruba", 5)

    controller._thread.join(timeout=5)
    assert controller._thread is first_thread
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 5


def test_replacement_trigger_never_starts_a_second_worker_while_one_is_active(app_runtime, monkeypatch):
    client, runtime = app_runtime
    db.set_setting("maintain_target", "1")
    db.set_setting("target:yoruba", "30")
    _seed_accepted(25)
    ids = [row["id"] for row in client.get("/api/movies?language=yoruba&status=ALL").get_json()]

    started = []
    original_start = runtime.discovery.start

    def tracking_start(language, target, provider="youtube"):
        started.append((language, target))
        return original_start(language, target, provider=provider)

    monkeypatch.setattr(runtime.discovery, "start", tracking_start)
    # Simulate the worker already being active when the reject request lands.
    monkeypatch.setattr(runtime.discovery, "status", "RUNNING")
    monkeypatch.setattr(runtime.discovery, "_thread", threading.current_thread())

    r = client.post(f"/api/movies/{ids[0]}/reject", json={"reason": "USER_REJECTED"})
    assert r.get_json()["ok"] is True
    assert started == []
