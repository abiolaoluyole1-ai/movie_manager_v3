"""Focused tests for the batch-readiness fixes:
- download_quality persists through /api/settings
- the download-source and local-media routes resolve correctly
- Retry Download works for both direct-HTTP and YouTube/YTDLP movies
- a completed movie can never be accidentally requeued by Retry
- YouTube's "not a bot" access-block is classified/cleaned, not dumped raw
- a blocked movie is not retried in a rapid loop
"""
from pathlib import Path
from uuid import uuid4

import pytest

from movie_manager import db
from movie_manager.config import (
    YOUTUBE_BLOCKED_COOLDOWN_SECONDS, YOUTUBE_BLOCKED_MESSAGE, is_youtube_blocked_error,
)
from movie_manager.download import DownloadController, YtDlpDownloadBackend
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


def _seed_ready_http_movie(video_id, language="yoruba", title=None):
    db.upsert_movie({
        "language": language, "video_id": video_id, "title": title or f"Movie {video_id}",
        "normalised_title": (title or video_id).lower(), "description": "A Yoruba movie.",
        "channel_id": "channel", "channel_title": "Yoruba Channel",
        "duration_seconds": 4200, "youtube_url": f"https://example.test/{video_id}",
        "status": "ACCEPTED", "download_status": "READY",
        "download_url": f"https://example.test/{video_id}.mp4",
    })
    return db.get_movie_id(language, video_id)


def _seed_youtube_movie(video_id, language="yoruba", title=None, download_status="READY"):
    """A YouTube/YTDLP movie: no permanent download_url -- identity lives in
    video_id/youtube_url, and YtDlpDownloadBackend resolves streams fresh at
    download time."""
    db.upsert_movie({
        "language": language, "video_id": video_id, "title": title or f"Movie {video_id}",
        "normalised_title": (title or video_id).lower(), "description": "A Yoruba movie.",
        "channel_id": "channel", "channel_title": "Yoruba Channel",
        "duration_seconds": 4200, "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "status": "ACCEPTED", "download_status": "READY",
    })
    movie_id = db.get_movie_id(language, video_id)
    db.queue_movies_for_download(language, [movie_id])
    if download_status != "READY":
        db.update_download(movie_id, download_status=download_status,
                            status="DOWNLOADED" if download_status == "DOWNLOADED" else "ACCEPTED")
    return movie_id


# ---------------------------------------------------------------------------
# 1. download_quality persistence
# ---------------------------------------------------------------------------

def test_download_quality_saves_and_reloads(app_runtime):
    client, _runtime = app_runtime

    r = client.post("/api/settings", json={"download_quality": "720"})
    assert r.get_json()["settings"]["download_quality"] == "720"

    # Reload from a fresh bootstrap-style read, not just the echoed response.
    assert db.get_setting("download_quality") == "720"
    r2 = client.get("/api/bootstrap")
    assert r2.get_json()["settings"]["download_quality"] == "720"


def test_download_quality_rejects_invalid_value_with_default(app_runtime):
    client, _runtime = app_runtime
    r = client.post("/api/settings", json={"download_quality": "4k"})
    assert r.get_json()["settings"]["download_quality"] == "1080"


def test_download_quality_default_is_1080(app_runtime):
    client, _runtime = app_runtime
    r = client.get("/api/bootstrap")
    assert r.get_json()["settings"]["download_quality"] == "1080"


def test_settings_update_does_not_disturb_unrelated_settings(app_runtime):
    client, _runtime = app_runtime
    client.post("/api/settings", json={"max_concurrent_downloads": "5"})
    r = client.post("/api/settings", json={"download_quality": "480"})
    assert r.get_json()["settings"]["max_concurrent_downloads"] == "5"


# ---------------------------------------------------------------------------
# 2. Route shape: download-source and local-media must resolve to our
# handlers (a truly broken/backslash route would 404 with Flask's default
# HTML page instead of our JSON body).
# ---------------------------------------------------------------------------

def test_download_source_route_resolves(app_runtime):
    client, _runtime = app_runtime
    r = client.post("/api/movies/999999/download-source", json={"download_url": "not-a-url"})
    assert r.status_code == 400
    body = r.get_json()
    assert body is not None
    assert "HTTP/HTTPS direct file URL" in body["error"]


def test_local_media_route_resolves(app_runtime):
    client, _runtime = app_runtime
    r = client.get("/api/movies/999999/local-media")
    assert r.status_code == 404
    body = r.get_json()
    assert body is not None
    assert body["error"] == "Completed local movie file not found."


# ---------------------------------------------------------------------------
# 3. Retry Download: direct-HTTP preserved, YouTube/YTDLP now works,
# completed movies can never be requeued.
# ---------------------------------------------------------------------------

def test_direct_http_retry_still_works(app_runtime):
    client, _runtime = app_runtime
    movie_id = _seed_ready_http_movie("http1")
    db.update_download(movie_id, download_status="FAILED", download_error="Connection reset")

    r = client.post(f"/api/movies/{movie_id}/retry-download")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    row = db.get_movie(movie_id)
    assert row["download_status"] == "READY"
    assert row["download_error"] is None


def test_youtube_retry_works_without_download_url(app_runtime):
    client, _runtime = app_runtime
    movie_id = _seed_youtube_movie("yt1", download_status="FAILED")
    db.update_download(movie_id, download_error="ERROR: [youtube] yt1: some transient failure")

    row_before = db.get_movie(movie_id)
    assert not row_before.get("download_url")
    assert row_before["provider"] == "youtube"
    assert row_before["download_backend"] == "YTDLP"

    r = client.post(f"/api/movies/{movie_id}/retry-download")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    row = db.get_movie(movie_id)
    assert row["download_status"] == "READY"
    assert row["download_error"] is None
    assert not row.get("download_url")  # never required/fabricated


def test_completed_youtube_movie_cannot_be_requeued(app_runtime):
    client, _runtime = app_runtime
    movie_id = _seed_youtube_movie("yt2", download_status="DOWNLOADED")

    r = client.post(f"/api/movies/{movie_id}/retry-download")
    assert r.status_code == 400
    assert "already been downloaded" in r.get_json()["error"]

    row = db.get_movie(movie_id)
    assert row["download_status"] == "DOWNLOADED"


def test_completed_direct_http_movie_cannot_be_requeued(app_runtime):
    client, _runtime = app_runtime
    movie_id = _seed_ready_http_movie("http2")
    db.update_download(movie_id, download_status="DOWNLOADED", status="DOWNLOADED")

    r = client.post(f"/api/movies/{movie_id}/retry-download")
    assert r.status_code == 400
    row = db.get_movie(movie_id)
    assert row["download_status"] == "DOWNLOADED"


def test_retry_download_404_for_unknown_movie(app_runtime):
    client, _runtime = app_runtime
    r = client.post("/api/movies/999999/retry-download")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 4. YouTube bot-detection: classified cleanly, no traceback dump, no rapid
# automatic retry loop, and manual retry is cooled down.
# ---------------------------------------------------------------------------

def test_is_youtube_blocked_error_detects_real_provider_message():
    real_error = (
        "ERROR: [youtube] tJ8efUMisWk: Sign in to confirm you’re not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication."
    )
    assert is_youtube_blocked_error(real_error)


def test_is_youtube_blocked_error_does_not_false_positive_on_other_errors():
    assert not is_youtube_blocked_error("Connection reset by peer")
    assert not is_youtube_blocked_error("Output verification failed: yt-dlp did not create an MP4 file.")


def test_youtube_blocked_error_stores_clean_message_not_raw_traceback(isolated_db, monkeypatch, tmp_path):
    db.init_db()
    db.set_setting("download_root", str(tmp_path))
    movie_id = _seed_youtube_movie("yt3")
    movie = db.get_movie(movie_id)

    raw_traceback = (
        "ERROR: [youtube] yt3: Sign in to confirm you’re not a bot. Use --cookies-from-browser "
        "or --cookies for the authentication. See https://github.com/yt-dlp/yt-dlp/wiki/FAQ "
        "for how to manually pass cookies. Also see https://github.com/yt-dlp/yt-dlp/wiki/Extractors"
    )

    def boom(self, movie, worker_id, final, part):
        raise RuntimeError(raw_traceback)

    monkeypatch.setattr(YtDlpDownloadBackend, "download", boom)

    controller = DownloadController()
    controller._download_youtube_one(movie, worker_id=0)

    row = db.get_movie(movie_id)
    assert row["download_status"] == "FAILED"
    assert row["download_error"] == YOUTUBE_BLOCKED_MESSAGE
    assert "github.com" not in row["download_error"]
    assert "Sign in to confirm" not in row["download_error"]


def test_youtube_blocked_movie_is_not_retried_automatically(isolated_db, monkeypatch, tmp_path):
    """A blocked movie must not corrupt the queue or get pulled straight
    back into an automatic retry loop -- it stays FAILED until a human
    retries it."""
    db.init_db()
    db.set_setting("download_root", str(tmp_path))
    movie_id = _seed_youtube_movie("yt4")

    call_count = {"n": 0}

    def boom(self, movie, worker_id, final, part):
        call_count["n"] += 1
        raise RuntimeError("Sign in to confirm you're not a bot.")

    monkeypatch.setattr(YtDlpDownloadBackend, "download", boom)

    controller = DownloadController()
    controller.start("yoruba", concurrency=1)
    try:
        deadline_ok = False
        import time
        deadline = time.time() + 5
        while time.time() < deadline:
            if db.get_movie(movie_id)["download_status"] == "FAILED":
                deadline_ok = True
                break
            time.sleep(0.05)
        assert deadline_ok, "worker never reached FAILED state"
        # Give the poll loop a couple more cycles: a real bug here would
        # show up as call_count climbing past 1 (an automatic re-download).
        time.sleep(1.0)
    finally:
        controller.stop()

    assert call_count["n"] == 1
    row = db.get_movie(movie_id)
    assert row["download_status"] == "FAILED"
    assert row["download_error"] == YOUTUBE_BLOCKED_MESSAGE


def test_manual_retry_is_cooled_down_right_after_a_block(app_runtime):
    client, _runtime = app_runtime
    movie_id = _seed_youtube_movie("yt5", download_status="FAILED")
    db.update_download(movie_id, download_error=YOUTUBE_BLOCKED_MESSAGE)

    r = client.post(f"/api/movies/{movie_id}/retry-download")
    assert r.status_code == 429
    assert "Wait about" in r.get_json()["error"]
    row = db.get_movie(movie_id)
    assert row["download_status"] == "FAILED"


def test_manual_retry_allowed_again_after_cooldown_elapses(app_runtime):
    client, _runtime = app_runtime
    movie_id = _seed_youtube_movie("yt6", download_status="FAILED")
    db.update_download(movie_id, download_error=YOUTUBE_BLOCKED_MESSAGE)
    # Simulate the cooldown window having already passed.
    with db.connect() as conn:
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(seconds=YOUTUBE_BLOCKED_COOLDOWN_SECONDS + 5)).isoformat()
        conn.execute("UPDATE movies SET updated_at=? WHERE id=?", (past, movie_id))
        conn.commit()

    r = client.post(f"/api/movies/{movie_id}/retry-download")
    assert r.status_code == 200
    row = db.get_movie(movie_id)
    assert row["download_status"] == "READY"
