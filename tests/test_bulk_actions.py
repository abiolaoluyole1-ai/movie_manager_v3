from pathlib import Path
from uuid import uuid4

import pytest

from movie_manager import db
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
def client(monkeypatch, isolated_db):
    db.init_db()
    monkeypatch.setattr("movie_manager.webapp.runtime", Runtime())
    return create_app().test_client()


def _movie(video_id, title, language="yoruba", status="ACCEPTED", download_status="NOT_READY"):
    return {
        "language": language, "video_id": video_id, "title": title,
        "normalised_title": title.lower(), "description": "A Yoruba movie.",
        "channel_id": "channel", "channel_title": "Yoruba Channel",
        "duration_seconds": 4200, "youtube_url": f"https://example.test/{video_id}",
        "status": status, "download_status": download_status,
    }


def _seed_accepted(n, language="yoruba", prefix="acc"):
    for i in range(n):
        db.upsert_movie(_movie(f"{prefix}{i}", f"Accepted Movie {i}", language=language))


def _ids_for(client, language="yoruba", status="ALL", search=""):
    r = client.get(f"/api/movies?language={language}&status={status}&search={search}").get_json()
    return [row["id"] for row in r]


def test_bulk_reject_five_accepted_movies_marks_user_rejected(client):
    _seed_accepted(5)
    ids = _ids_for(client)

    r = client.post("/api/movies/bulk/reject", json={"ids": ids, "language": "yoruba", "reason": "USER_REJECTED"}).get_json()

    assert r["ok"] is True
    assert r["requested"] == 5
    assert r["updated"] == 5
    assert r["skipped"] == 0
    assert r["not_found"] == 0
    rows = client.get("/api/movies?language=yoruba&status=REJECTED").get_json()
    assert len(rows) == 5
    assert all(row["rejection_reason"] == "USER_REJECTED" for row in rows)


def test_bulk_reject_decreases_accepted_count(client):
    _seed_accepted(5)
    ids = _ids_for(client)
    before = client.get("/api/bootstrap").get_json()["counts"]["accepted"]

    client.post("/api/movies/bulk/reject", json={"ids": ids[:3], "language": "yoruba"})

    after = client.get("/api/bootstrap").get_json()["counts"]["accepted"]
    assert before == 5
    assert after == 2


def test_bulk_reject_triggers_replacement_discovery_only_once(client, monkeypatch):
    db.set_setting("maintain_target", "1")
    db.set_setting("target:yoruba", "30")
    _seed_accepted(30)
    ids = _ids_for(client)

    starts = []
    from movie_manager.webapp import runtime as app_runtime
    monkeypatch.setattr(
        app_runtime, "start_discovery",
        lambda language, target, provider="youtube": starts.append((language, target))
    )

    r = client.post("/api/movies/bulk/reject", json={"ids": ids[:5], "language": "yoruba"}).get_json()

    assert r["updated"] == 5
    assert r["replacement_triggered"] is True
    assert len(starts) == 1
    assert starts[0] == ("yoruba", 30)


def test_bulk_reject_does_not_trigger_replacement_when_maintain_target_off(client, monkeypatch):
    db.set_setting("maintain_target", "0")
    db.set_setting("target:yoruba", "30")
    _seed_accepted(30)
    ids = _ids_for(client)

    from movie_manager.webapp import runtime as app_runtime
    monkeypatch.setattr(app_runtime, "start_discovery", lambda *a, **k: pytest.fail("should not start discovery"))

    r = client.post("/api/movies/bulk/reject", json={"ids": ids[:5], "language": "yoruba"}).get_json()

    assert r["replacement_triggered"] is False


def test_bulk_wrong_language_stores_wrong_language_reason(client):
    _seed_accepted(3)
    ids = _ids_for(client)

    r = client.post("/api/movies/bulk/reject", json={"ids": ids, "language": "yoruba", "reason": "WRONG_LANGUAGE"}).get_json()

    assert r["updated"] == 3
    rows = client.get("/api/movies?language=yoruba&status=REJECTED").get_json()
    assert all(row["rejection_reason"] == "WRONG_LANGUAGE" for row in rows)


def test_bulk_restore_restores_rejected_movies(client):
    _seed_accepted(4)
    ids = _ids_for(client)
    client.post("/api/movies/bulk/reject", json={"ids": ids, "language": "yoruba"})

    r = client.post("/api/movies/bulk/restore", json={"ids": ids, "language": "yoruba"}).get_json()

    assert r["ok"] is True
    assert r["updated"] == 4
    rows = client.get("/api/movies?language=yoruba&status=ACCEPTED").get_json()
    assert len(rows) == 4
    assert all(row["rejection_reason"] is None for row in rows)


def test_bulk_restore_does_not_create_duplicate_rows(client):
    _seed_accepted(2)
    ids = _ids_for(client)
    client.post("/api/movies/bulk/reject", json={"ids": ids, "language": "yoruba"})

    client.post("/api/movies/bulk/restore", json={"ids": ids, "language": "yoruba"})

    all_rows = client.get("/api/movies?language=yoruba&status=ALL").get_json()
    assert len(all_rows) == 2


def test_duplicate_ids_in_request_do_not_cause_duplicate_operations(client):
    _seed_accepted(2)
    ids = _ids_for(client)
    duplicated = [ids[0], ids[0], ids[0], ids[1]]

    r = client.post("/api/movies/bulk/reject", json={"ids": duplicated, "language": "yoruba"}).get_json()

    assert r["requested"] == 2
    assert r["updated"] == 2


def test_missing_ids_are_handled_safely(client):
    _seed_accepted(2)
    ids = _ids_for(client)
    missing_id = max(ids) + 999

    r = client.post("/api/movies/bulk/reject", json={"ids": ids + [missing_id], "language": "yoruba"}).get_json()

    assert r["ok"] is True
    assert r["requested"] == 3
    assert r["updated"] == 2
    assert r["not_found"] == 1


def test_non_integer_ids_do_not_crash(client):
    _seed_accepted(2)
    ids = _ids_for(client)

    r = client.post("/api/movies/bulk/reject", json={"ids": ids + ["abc", None, 4.9], "language": "yoruba"}).get_json()

    assert r["ok"] is True
    assert r["updated"] == 2


def test_mixed_language_ids_cannot_affect_other_language_catalogue(client):
    _seed_accepted(2, language="yoruba")
    _seed_accepted(2, language="igbo", prefix="igbo")
    yoruba_ids = _ids_for(client, language="yoruba")
    igbo_ids = _ids_for(client, language="igbo")

    r = client.post("/api/movies/bulk/reject", json={"ids": yoruba_ids + igbo_ids, "language": "yoruba"}).get_json()

    assert r["updated"] == 2
    assert r["not_found"] == 2
    igbo_rows = client.get("/api/movies?language=igbo&status=ALL").get_json()
    assert all(row["status"] == "ACCEPTED" for row in igbo_rows)


def test_bulk_reject_is_transactional_and_safe(client):
    _seed_accepted(3)
    ids = _ids_for(client)

    result = db.bulk_reject_movies("yoruba", ids, "USER_REJECTED")

    assert result["updated"] == 3
    rows = client.get("/api/movies?language=yoruba&status=REJECTED").get_json()
    assert len(rows) == 3


def test_already_rejected_movies_are_skipped_not_reprocessed(client):
    _seed_accepted(2)
    ids = _ids_for(client)
    client.post("/api/movies/bulk/reject", json={"ids": ids, "language": "yoruba", "reason": "USER_REJECTED"})

    r = client.post("/api/movies/bulk/reject", json={"ids": ids, "language": "yoruba", "reason": "WRONG_LANGUAGE"}).get_json()

    assert r["updated"] == 0
    assert r["skipped"] == 2
    rows = client.get("/api/movies?language=yoruba&status=REJECTED").get_json()
    assert all(row["rejection_reason"] == "USER_REJECTED" for row in rows)


def test_search_status_filter_and_ids_endpoint_work_together(client):
    db.upsert_movie(_movie("m1", "Searchable Feature One"))
    db.upsert_movie(_movie("m2", "Another Feature"))
    db.upsert_movie(_movie("m3", "Searchable Rejected", status="REJECTED"))

    r = client.get("/api/movies/ids?language=yoruba&status=ACCEPTED&search=Searchable").get_json()

    ids = _ids_for(client, status="ACCEPTED", search="Searchable")
    assert r["ids"] == ids
    assert r["count"] == 1


def test_select_page_uses_currently_loaded_movies_endpoint(client):
    _seed_accepted(5)

    rows = client.get("/api/movies?language=yoruba&status=ALL&limit=120").get_json()

    assert len(rows) == 5
    assert {row["id"] for row in rows} == set(_ids_for(client))


def test_select_all_filtered_results_returns_all_matching_ids(client):
    _seed_accepted(150)

    r = client.get("/api/movies/ids?language=yoruba&status=ACCEPTED").get_json()

    assert r["count"] == 150
    assert len(r["ids"]) == 150


def test_bulk_endpoint_rejects_non_list_ids_payload(client):
    r = client.post("/api/movies/bulk/reject", json={"ids": "not-a-list", "language": "yoruba"})

    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_bulk_endpoint_rejects_invalid_reason(client):
    _seed_accepted(1)
    ids = _ids_for(client)

    r = client.post("/api/movies/bulk/reject", json={"ids": ids, "language": "yoruba", "reason": "MADE_UP"})

    assert r.status_code == 400


def test_existing_movies_endpoint_filters_still_work_alongside_bulk_ids(client):
    db.upsert_movie(_movie("a1", "Accepted A"))
    db.upsert_movie(_movie("a2", "Accepted B", status="DOWNLOADED", download_status="DOWNLOADED"))
    db.upsert_movie(_movie("a3", "Rejected C", status="REJECTED"))

    all_rows = client.get("/api/movies?language=yoruba&status=ALL").get_json()
    accepted_rows = client.get("/api/movies?language=yoruba&status=ACCEPTED").get_json()
    downloaded_ids = client.get("/api/movies/ids?language=yoruba&status=DOWNLOADED").get_json()["ids"]

    assert len(all_rows) == 3
    assert len(accepted_rows) == 1
    assert len(downloaded_ids) == 1
