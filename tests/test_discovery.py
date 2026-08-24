import logging

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
        "rejected_wrong_language": 3,
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
            "rejected_wrong_language": 3,
            "duplicates_skipped": 3, "api_requests": 2,
            "network_retries": 1, "current_query": "old Yoruba full movie",
        },
    })

    snapshot = controller.restore_latest("yoruba")

    assert snapshot["status"] == "COMPLETED"
    assert snapshot["stats"]["candidates_scanned"] == 50
    assert snapshot["stats"]["api_requests"] == 2
    assert snapshot["stats"]["rejected_wrong_language"] == 3
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
        "rejected_wrong_language": 2,
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
    assert first["stats"]["rejected_wrong_language"] == 2
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


def _video_item(title, description="A full Yoruba drama."):
    return {
        "id": "test-video-id",
        "snippet": {"title": title, "description": description, "thumbnails": {}},
        "contentDetails": {"duration": "PT1H17M"},
        "status": {"embeddable": True},
    }


def _evaluate_movie(monkeypatch, title, description="A feature drama.", **snippet_fields):
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *args, **kwargs: None)
    item = _video_item(title, description)
    item["snippet"].update(snippet_fields)
    return DiscoveryController()._evaluate(item, "Yoruba full movie", 3600)


@pytest.mark.parametrize("title", [
    "ALAGBADA INA - BEST OF FADEYI AND ABIJA NIGERIAN YORUBA MOVIE",
    "Yoruba Comedy Compilation",
])
def test_compilation_titles_are_rejected(monkeypatch, title):
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *args, **kwargs: None)
    movie, result = DiscoveryController()._evaluate(_video_item(title), "Yoruba full movie", 3600)

    assert result == "NOT_MOVIE"
    assert movie["status"] == "REJECTED"
    assert movie["rejection_reason"].startswith("COMPILATION_TITLE:")


def test_normal_full_movie_title_remains_acceptable(monkeypatch):
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *args, **kwargs: None)
    movie, result = DiscoveryController()._evaluate(
        _video_item("ABELA PUPA | CLASSIC YORUBA MOVIE", "A collection of family memories."),
        "Yoruba full movie", 3600,
    )

    assert result == "ACCEPTED"
    assert movie["status"] == "ACCEPTED"


def test_explicit_yoruba_audio_language_is_accepted(monkeypatch):
    movie, result = _evaluate_movie(
        monkeypatch, "The Promise", defaultAudioLanguage="yo-NG"
    )

    assert result == "ACCEPTED"
    assert movie["default_audio_language"] == "yo-NG"


def test_explicit_english_audio_without_yoruba_evidence_is_rejected(monkeypatch):
    movie, result = _evaluate_movie(
        monkeypatch, "The Promise", defaultAudioLanguage="en"
    )

    assert result == "WRONG_LANGUAGE"
    assert movie["rejection_reason"] == "WRONG_LANGUAGE"


def test_english_looking_title_with_yoruba_metadata_is_not_rejected(monkeypatch):
    movie, result = _evaluate_movie(
        monkeypatch, "A Mother's Promise", defaultLanguage="yoruba"
    )

    assert result == "ACCEPTED"
    assert movie["default_language"] == "yoruba"


def test_generic_nollywood_result_without_yoruba_evidence_is_rejected(monkeypatch):
    movie, result = _evaluate_movie(
        monkeypatch, "Nollywood Family Drama", "A Nigerian feature film."
    )

    assert result == "WRONG_LANGUAGE"
    assert movie["rejection_reason"] == "WRONG_LANGUAGE"


def test_missing_language_metadata_with_yoruba_evidence_can_pass(monkeypatch):
    movie, result = _evaluate_movie(
        monkeypatch, "A Mother's Promise", "An award-winning Yoruba movie.", channelTitle="Cinema Hub"
    )

    assert result == "ACCEPTED"
    assert movie["default_audio_language"] is None


def test_wrong_language_counter_increments(monkeypatch):
    controller = DiscoveryController()
    controller._record_result("WRONG_LANGUAGE")

    assert controller.stats["rejected_wrong_language"] == 1


def _stored_movie(video_id, title, status="ACCEPTED", download_status="NOT_READY"):
    return {
        "language": "yoruba", "video_id": video_id, "title": title,
        "normalised_title": title.lower(), "description": "Yoruba movie.",
        "channel_id": "channel", "channel_title": "Yoruba Channel",
        "duration_seconds": 4200, "youtube_url": f"https://example.test/{video_id}",
        "status": status, "download_status": download_status,
    }


def test_movies_api_filters_status_and_search_together(monkeypatch, isolated_db):
    db.init_db()
    db.upsert_movie(_stored_movie("accepted", "Searchable Accepted"))
    db.upsert_movie(_stored_movie("queued", "Queued Feature", status="QUEUED", download_status="QUEUED"))
    db.upsert_movie(_stored_movie("downloading", "Downloading Feature", status="DOWNLOADING", download_status="DOWNLOADING"))
    db.upsert_movie(_stored_movie("rejected", "Rejected Feature", status="REJECTED"))
    db.upsert_movie(_stored_movie("downloaded", "Downloaded Feature", status="DOWNLOADED", download_status="DOWNLOADED"))
    monkeypatch.setattr("movie_manager.webapp.runtime", Runtime())
    client = create_app().test_client()

    all_rows = client.get("/api/movies?language=yoruba&status=ALL").get_json()
    accepted_rows = client.get("/api/movies?language=yoruba&status=ACCEPTED").get_json()
    rejected_rows = client.get("/api/movies?language=yoruba&status=REJECTED").get_json()
    downloaded_rows = client.get("/api/movies?language=yoruba&status=DOWNLOADED").get_json()
    searched_rows = client.get("/api/movies?language=yoruba&status=ACCEPTED&search=Searchable").get_json()

    assert len(all_rows) == 5
    assert {row["status"] for row in accepted_rows} == {"ACCEPTED", "QUEUED", "DOWNLOADING"}
    assert [row["status"] for row in rejected_rows] == ["REJECTED"]
    assert [row["download_status"] for row in downloaded_rows] == ["DOWNLOADED"]
    assert [row["title"] for row in searched_rows] == ["Searchable Accepted"]
