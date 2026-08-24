import pytest

from movie_manager import db
from movie_manager.webapp import create_app


def movie(video_id="one"):
    return {
        "language": "yoruba", "video_id": video_id, "title": "Yoruba Film",
        "youtube_url": f"https://example.test/{video_id}", "duration_seconds": 3600,
        "status": "ACCEPTED", "download_status": "DOWNLOADED",
        "file_path": r"C:\Movies\kept.mp4",
    }


def test_delete_from_library_forgets_record_but_not_file(isolated_db):
    db.init_db()
    saved = isolated_db.with_suffix(".kept.mp4")
    saved.write_bytes(b"movie")
    data = movie()
    data["file_path"] = str(saved)
    db.upsert_movie(data)
    movie_id = db.get_movie_id("yoruba", "one")

    assert db.delete_movie_from_library(movie_id)["language"] == "yoruba"
    assert db.get_movie(movie_id) is None
    assert saved.exists()
    db.upsert_movie(data)  # deletion permits rediscovery of the same provider item
    assert db.get_movie_id("yoruba", "one") is not None
    saved.unlink(missing_ok=True)


def test_clear_library_preserves_settings_and_provider_memory(isolated_db):
    db.init_db()
    db.set_setting("download_root", r"C:\Movies")
    db.upsert_movie(movie())
    db.set_provider_cache_entry("internet_archive", "yoruba", "old", "REJECTED")

    assert db.clear_movie_library() == 1
    assert db.count_movies("yoruba") == 0
    assert db.get_setting("download_root") == r"C:\Movies"
    assert db.get_provider_cache_entry("internet_archive", "yoruba", "old")


def test_start_fresh_clears_memory_history_and_preserves_settings_and_file(isolated_db):
    db.init_db()
    saved = isolated_db.with_suffix(".kept.mp4")
    saved.write_bytes(b"movie")
    data = movie()
    data["file_path"] = str(saved)
    db.upsert_movie(data)
    db.set_setting("download_root", str(isolated_db.parent))
    db.save_search_state("yoruba", "yoruba movie", "next", False)
    db.set_provider_cache_entry("internet_archive", "yoruba", "old", "REJECTED")
    db.create_job("DISCOVERY", "yoruba", status="COMPLETED")

    assert db.start_fresh() == 1
    assert db.count_movies("yoruba") == 0
    assert db.get_search_state("yoruba", "yoruba movie") == (None, False)
    assert db.get_provider_cache_entry("internet_archive", "yoruba", "old") is None
    assert db.get_latest_job("DISCOVERY", "yoruba") is None
    assert db.get_setting("download_root") == str(isolated_db.parent)
    assert saved.exists()
    saved.unlink(missing_ok=True)


def test_reset_endpoints_refuse_while_worker_active(isolated_db, monkeypatch):
    db.init_db()
    app = create_app()
    monkeypatch.setattr("movie_manager.webapp.runtime.snapshot", lambda: {
        "discovery": {"status": "RUNNING"}, "downloads": {"status": "IDLE"},
        "supply_scan": {"status": "IDLE"},
    })
    client = app.test_client()
    assert client.post("/api/settings/clear-library", json={}).status_code == 409
    assert client.post("/api/settings/start-fresh", json={}).status_code == 409
