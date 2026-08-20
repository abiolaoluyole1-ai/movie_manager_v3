import logging
from pathlib import Path
from uuid import uuid4

import pytest

from movie_manager import db
from movie_manager.discovery import DiscoveryController, SEARCH_URL, YouTubeAPIError
from movie_manager.runtime import Runtime
from movie_manager.webapp import create_app


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def isolated_db(monkeypatch):
    path = Path.cwd() / f".movie-manager-test-{uuid4().hex}.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    yield path
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.unlink(missing_ok=True)
        except PermissionError:
            # SQLite may retain a Windows handle until the test process exits.
            pass


def test_permanent_api_error_is_not_retried_and_key_is_redacted(monkeypatch, caplog):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params, timeout))
        return FakeResponse(400, {
            "error": {
                "message": "Request contains an invalid argument.",
                "errors": [{"reason": "badRequest"}],
            }
        })

    monkeypatch.setattr("movie_manager.discovery.requests.get", fake_get)
    controller = DiscoveryController()
    params = {"part": "snippet", "key": "private-test-key"}

    with caplog.at_level(logging.ERROR), pytest.raises(YouTubeAPIError) as caught:
        controller._request(SEARCH_URL, params)

    assert caught.value.status_code == 400
    assert len(calls) == 1
    assert controller.stats["network_retries"] == 0
    assert "private-test-key" not in caplog.text
    assert "<redacted>" in caplog.text


def test_search_retries_without_rejected_optional_language(monkeypatch):
    controller = DiscoveryController()
    calls = []

    def fake_request(url, params):
        calls.append(dict(params))
        if "relevanceLanguage" in params:
            raise YouTubeAPIError(400, "badRequest", "Request contains an invalid argument.")
        return FakeResponse(200, {"items": []})

    monkeypatch.setattr(controller, "_request", fake_request)
    response = controller._search({
        "part": "snippet",
        "type": "video",
        "q": "Yoruba full movie",
        "relevanceLanguage": "yo",
        "key": "private-test-key",
    })

    assert response.status_code == 200
    assert len(calls) == 2
    assert calls[0]["relevanceLanguage"] == "yo"
    assert "relevanceLanguage" not in calls[1]
    assert calls[1]["q"] == "Yoruba full movie"


def test_completed_run_stats_are_persisted_with_last_query(isolated_db):
    db.init_db()
    stats = {
        "candidates_scanned": 50, "accepted": 10,
        "rejected_under_duration": 1, "rejected_not_movie": 2,
        "duplicates_skipped": 3, "api_requests": 2,
        "network_retries": 1, "current_query": "classic Yoruba full movie",
    }
    job_id = db.create_job("DISCOVERY", "yoruba", 10, "RUNNING", stats, "Searching")
    db.update_job(job_id, status="COMPLETED", stats=stats, message="Target reached: 10/10")

    job = db.get_latest_job("DISCOVERY", "yoruba")

    assert job["status"] == "COMPLETED"
    assert job["stats"] == stats
    assert job["message"] == "Target reached: 10/10"


def test_restore_latest_uses_persisted_stats(monkeypatch):
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.get_latest_job", lambda *args, **kwargs: {
        "language": "yoruba", "target": 10, "status": "COMPLETED",
        "message": "Target reached: 10/10",
        "stats": {
            "candidates_scanned": 50, "accepted": 10,
            "rejected_under_duration": 1, "rejected_not_movie": 2,
            "duplicates_skipped": 3, "api_requests": 2,
            "network_retries": 1, "current_query": "old Yoruba full movie",
        },
    })

    snapshot = controller.restore_latest("yoruba")

    assert snapshot["status"] == "COMPLETED"
    assert snapshot["stats"]["candidates_scanned"] == 50
    assert snapshot["stats"]["api_requests"] == 2
    assert snapshot["current_query"] == "old Yoruba full movie"


def test_restore_legacy_job_uses_latest_saved_source_query(monkeypatch):
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.get_latest_job", lambda *args, **kwargs: {
        "language": "yoruba", "target": 10, "status": "COMPLETED",
        "message": "Target reached: 10/10", "stats": {"api_requests": 2},
    })
    monkeypatch.setattr(
        "movie_manager.discovery.get_latest_source_query", lambda language: "old Yoruba full movie"
    )

    snapshot = controller.restore_latest("yoruba")

    assert snapshot["current_query"] == "old Yoruba full movie"


def test_bootstrap_restores_latest_discovery_stats_after_refresh(monkeypatch, isolated_db):
    db.init_db()
    stats = {
        "candidates_scanned": 50, "accepted": 10,
        "rejected_under_duration": 1, "rejected_not_movie": 0,
        "duplicates_skipped": 0, "api_requests": 2,
        "network_retries": 0, "current_query": "old Yoruba full movie",
    }
    job_id = db.create_job("DISCOVERY", "yoruba", 10, "COMPLETED", stats, "Target reached: 10/10")
    db.update_job(job_id, status="COMPLETED", stats=stats, message="Target reached: 10/10")
    monkeypatch.setattr("movie_manager.webapp.runtime", Runtime())
    app = create_app()

    first = app.test_client().get("/api/bootstrap").get_json()["runtime"]["discovery"]
    second = app.test_client().get("/api/bootstrap").get_json()["runtime"]["discovery"]

    assert first["stats"]["candidates_scanned"] == 50
    assert first["stats"]["api_requests"] == 2
    assert first["current_query"] == "old Yoruba full movie"
    assert second == first


def test_new_discovery_run_starts_with_zero_run_counters(monkeypatch):
    controller = DiscoveryController()
    created = []

    monkeypatch.setattr("movie_manager.discovery.count_movies", lambda *args, **kwargs: 3)
    monkeypatch.setattr("movie_manager.discovery.create_job", lambda *args: created.append(args) or 99)
    monkeypatch.setattr("movie_manager.discovery.threading.Thread", lambda **kwargs: type("Thread", (), {"start": lambda self: None, "is_alive": lambda self: False})())

    controller.start("yoruba", 10)

    assert created
    assert created[0][4]["accepted"] == 0
    assert created[0][4]["candidates_scanned"] == 0
    assert created[0][4]["current_query"] == ""


def test_satisfied_target_does_not_create_a_zero_stat_job(monkeypatch):
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.count_movies", lambda *args, **kwargs: 10)
    monkeypatch.setattr("movie_manager.discovery.create_job", lambda *args: pytest.fail("no job expected"))
    monkeypatch.setattr("movie_manager.discovery.get_latest_job", lambda *args, **kwargs: {
        "language": "yoruba", "target": 10, "status": "COMPLETED",
        "message": "Target reached: 10/10",
        "stats": {"candidates_scanned": 50, "api_requests": 2},
    })
    monkeypatch.setattr("movie_manager.discovery.get_latest_source_query", lambda language: "old Yoruba full movie")

    controller.start("yoruba", 10)

    assert controller.status == "COMPLETED"
    assert controller.stats["candidates_scanned"] == 50
    assert controller.stats["api_requests"] == 2
