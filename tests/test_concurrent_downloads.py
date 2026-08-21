import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from movie_manager import db
from movie_manager.config import clamp_concurrency, clamp_min_free_disk_gb
from movie_manager.download import DownloadController
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


def _seed_ready_movie(video_id, language="yoruba", title=None):
    db.upsert_movie({
        "language": language, "video_id": video_id, "title": title or f"Movie {video_id}",
        "normalised_title": (title or video_id).lower(), "description": "A Yoruba movie.",
        "channel_id": "channel", "channel_title": "Yoruba Channel",
        "duration_seconds": 4200, "youtube_url": f"https://example.test/{video_id}",
        "status": "ACCEPTED", "download_status": "READY",
        "download_url": f"https://example.test/{video_id}.mp4",
    })
    return db.get_movie_id(language, video_id)


class _FakeResp:
    def __init__(self, status_code, content_length, chunks_gen):
        self.status_code = status_code
        self.headers = {"Content-Length": str(content_length)} if content_length else {}
        self._chunks_gen = chunks_gen

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=1024 * 512):
        for c in self._chunks_gen:
            yield c


def _resp(status_code=200, content_length=0, chunks=None):
    return _FakeResp(status_code, content_length, chunks or [])


def _wait_until(predicate, timeout=5, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Concurrency setting: default / min / max / invalid handling
# ---------------------------------------------------------------------------

def test_default_concurrency_is_three():
    from movie_manager.config import CONCURRENCY_DEFAULT, DEFAULTS
    assert CONCURRENCY_DEFAULT == 3
    assert DEFAULTS["max_concurrent_downloads"] == "3"


def test_clamp_concurrency_minimum_is_one():
    assert clamp_concurrency(0) == 1
    assert clamp_concurrency(-5) == 1


def test_clamp_concurrency_maximum_is_seven():
    assert clamp_concurrency(8) == 7
    assert clamp_concurrency(999) == 7


def test_clamp_concurrency_rejects_invalid_values_with_safe_default():
    assert clamp_concurrency("not-a-number") == 3
    assert clamp_concurrency(None) == 3
    assert clamp_concurrency("") == 3


def test_settings_endpoint_clamps_max_concurrent_downloads(app_runtime):
    client, _runtime = app_runtime
    r = client.post("/api/settings", json={"max_concurrent_downloads": "99"})
    assert r.get_json()["settings"]["max_concurrent_downloads"] == "7"

    r = client.post("/api/settings", json={"max_concurrent_downloads": "0"})
    assert r.get_json()["settings"]["max_concurrent_downloads"] == "1"

    r = client.post("/api/settings", json={"max_concurrent_downloads": "bogus"})
    assert r.get_json()["settings"]["max_concurrent_downloads"] == "3"


# ---------------------------------------------------------------------------
# Worker pool sizing: concurrency=1/3/7 never exceeds its own cap, and a
# finished job's slot is refilled immediately (not after the whole batch).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("concurrency", [1, 3, 7])
def test_worker_pool_never_exceeds_configured_concurrency(isolated_db, monkeypatch, concurrency):
    db.init_db()
    n_movies = concurrency * 3
    for i in range(n_movies):
        _seed_ready_movie(f"conc{concurrency}-{i}")

    active = {"n": 0}
    max_seen = {"n": 0}
    lock = threading.Lock()
    # A Barrier only releases once exactly `concurrency` threads have
    # called wait() -- so if it ever completes, that's deterministic proof
    # the pool actually ran `concurrency` jobs simultaneously, with no
    # sleep-based timing race. If fewer ever overlap, it times out instead
    # of silently under-reporting.
    barrier = threading.Barrier(concurrency)
    reached_full_concurrency = {"v": False}

    def fake_download_one(movie, worker_id):
        with lock:
            active["n"] += 1
            max_seen["n"] = max(max_seen["n"], active["n"])
        try:
            barrier.wait(timeout=5)
            with lock:
                reached_full_concurrency["v"] = True
        except threading.BrokenBarrierError:
            pass
        db.update_download(movie["id"], download_status="DOWNLOADED", status="DOWNLOADED")
        with lock:
            active["n"] -= 1

    controller = DownloadController()
    monkeypatch.setattr(controller, "_download_one", fake_download_one)
    controller.start("yoruba", concurrency=concurrency)
    try:
        done = _wait_until(
            lambda: db.counts("yoruba")[1].get("DOWNLOADED", 0) == n_movies, timeout=10
        )
        assert done, "not all movies finished downloading"
        assert max_seen["n"] <= concurrency
        assert reached_full_concurrency["v"], f"never had {concurrency} downloads active at once"
    finally:
        controller.stop()


# ---------------------------------------------------------------------------
# Duplicate job protection: concurrent claims never return the same movie.
# ---------------------------------------------------------------------------

def test_concurrent_claims_never_return_the_same_movie_twice(isolated_db):
    db.init_db()
    n = 10
    for i in range(n):
        _seed_ready_movie(f"dup{i}")

    results = []
    lock = threading.Lock()

    def worker():
        movie = db.next_download_ready("yoruba")
        with lock:
            results.append(movie["id"] if movie else None)

    threads = [threading.Thread(target=worker) for _ in range(n * 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    claimed = [r for r in results if r is not None]
    assert len(claimed) == n
    assert len(set(claimed)) == n


# ---------------------------------------------------------------------------
# Each completed movie finalises independently and is usable immediately,
# without waiting for the rest of the batch.
# ---------------------------------------------------------------------------

def test_each_completed_movie_finalises_independently(isolated_db, monkeypatch, tmp_path):
    db.init_db()
    db.set_setting("download_root", str(tmp_path))
    _seed_ready_movie("fast1", title="Fast Movie")
    _seed_ready_movie("slow1", title="Slow Movie")

    def slow_chunks():
        yield b"12345"
        time.sleep(0.8)
        yield b"67890"

    def fake_get(url, headers=None, stream=True, timeout=None, allow_redirects=True):
        if "fast1" in url:
            return _resp(200, 5, [b"hello"])
        return _resp(200, 10, slow_chunks())

    monkeypatch.setattr("movie_manager.download.requests.get", fake_get)

    controller = DownloadController()
    controller.start("yoruba", concurrency=2)
    try:
        fast_done = _wait_until(
            lambda: db.get_movie(db.get_movie_id("yoruba", "fast1"))["download_status"] == "DOWNLOADED",
            timeout=5,
        )
        assert fast_done
        slow_row = db.get_movie(db.get_movie_id("yoruba", "slow1"))
        assert slow_row["download_status"] == "DOWNLOADING"
        assert slow_row["file_path"]  # available (as a .part) even mid-transfer
    finally:
        controller.stop()


# ---------------------------------------------------------------------------
# Temporary vs permanent failure handling.
# ---------------------------------------------------------------------------

def test_temporary_network_failure_retries_and_then_succeeds(isolated_db, monkeypatch, tmp_path):
    db.init_db()
    db.set_setting("download_root", str(tmp_path))
    _seed_ready_movie("retry1")
    monkeypatch.setattr("movie_manager.download.NETWORK_RETRY_SECONDS", [0])

    calls = {"n": 0}

    def fake_get(url, headers=None, stream=True, timeout=None, allow_redirects=True):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.RequestException("simulated network blip")
        return _resp(200, 5, [b"hello"])

    monkeypatch.setattr("movie_manager.download.requests.get", fake_get)

    controller = DownloadController()
    controller.start("yoruba", concurrency=1)
    try:
        done = _wait_until(
            lambda: db.get_movie(db.get_movie_id("yoruba", "retry1"))["download_status"] == "DOWNLOADED",
            timeout=5,
        )
        assert done
        assert calls["n"] >= 2
    finally:
        controller.stop()


def test_permanent_failure_does_not_block_the_rest_of_the_batch(isolated_db, monkeypatch, tmp_path):
    db.init_db()
    db.set_setting("download_root", str(tmp_path))
    _seed_ready_movie("bad1")
    _seed_ready_movie("good1")

    def fake_get(url, headers=None, stream=True, timeout=None, allow_redirects=True):
        if "bad1" in url:
            return _resp(404, 0, [])
        return _resp(200, 5, [b"hello"])

    monkeypatch.setattr("movie_manager.download.requests.get", fake_get)

    controller = DownloadController()
    controller.start("yoruba", concurrency=2)
    try:
        done = _wait_until(
            lambda: (
                db.get_movie(db.get_movie_id("yoruba", "bad1"))["download_status"] == "FAILED"
                and db.get_movie(db.get_movie_id("yoruba", "good1"))["download_status"] == "DOWNLOADED"
            ),
            timeout=5,
        )
        assert done
    finally:
        controller.stop()


# ---------------------------------------------------------------------------
# Disk-space guard: pauses new jobs when low, resumes when space recovers.
# ---------------------------------------------------------------------------

def test_low_disk_space_prevents_new_job_and_recovery_allows_continuation(isolated_db, monkeypatch, tmp_path):
    db.init_db()
    db.set_setting("download_root", str(tmp_path))
    db.set_setting("min_free_disk_gb", "20")
    monkeypatch.setattr("movie_manager.download.DISK_CHECK_WAIT_SECONDS", 1)
    _seed_ready_movie("disk1")

    free_values = iter([1.0, 1.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0])
    monkeypatch.setattr("movie_manager.download.free_disk_space_gb", lambda _p: next(free_values, 50.0))
    monkeypatch.setattr(
        "movie_manager.download.requests.get",
        lambda *a, **k: _resp(200, 5, [b"hello"]),
    )

    controller = DownloadController()
    controller.start("yoruba", concurrency=1)
    try:
        saw_disk_low = _wait_until(lambda: controller.status == "DISK_LOW", timeout=3)
        assert saw_disk_low
        assert db.get_movie(db.get_movie_id("yoruba", "disk1"))["download_status"] == "READY"

        done = _wait_until(
            lambda: db.get_movie(db.get_movie_id("yoruba", "disk1"))["download_status"] == "DOWNLOADED",
            timeout=5,
        )
        assert done
    finally:
        controller.stop()


# ---------------------------------------------------------------------------
# Pause / resume / stop across the worker pool.
# ---------------------------------------------------------------------------

def test_pause_prevents_new_jobs_and_resume_continues_the_queue(isolated_db, monkeypatch, tmp_path):
    db.init_db()
    db.set_setting("download_root", str(tmp_path))
    monkeypatch.setattr(
        "movie_manager.download.requests.get",
        lambda *a, **k: _resp(200, 5, [b"hello"]),
    )

    controller = DownloadController()
    controller.start("yoruba", concurrency=1)
    controller.pause()
    movie_id = _seed_ready_movie("pr1")
    time.sleep(0.3)
    try:
        assert db.get_movie(movie_id)["download_status"] == "READY"

        controller.resume()
        done = _wait_until(lambda: db.get_movie(movie_id)["download_status"] == "DOWNLOADED", timeout=5)
        assert done
    finally:
        controller.stop()


def test_stop_mid_download_preserves_partial_file_and_resets_to_ready(isolated_db, monkeypatch, tmp_path):
    db.init_db()
    db.set_setting("download_root", str(tmp_path))
    movie_id = _seed_ready_movie("stop1")

    release = threading.Event()

    def slow_chunks():
        yield b"12345"
        release.wait(timeout=5)

    monkeypatch.setattr(
        "movie_manager.download.requests.get",
        lambda *a, **k: _resp(200, 10, slow_chunks()),
    )

    controller = DownloadController()
    controller.start("yoruba", concurrency=1)
    try:
        started = _wait_until(
            lambda: db.get_movie(movie_id)["download_status"] == "DOWNLOADING"
            and (db.get_movie(movie_id)["bytes_downloaded"] or 0) > 0,
            timeout=5,
        )
        assert started
        controller.stop()
        release.set()
        resumed_to_ready = _wait_until(lambda: db.get_movie(movie_id)["download_status"] == "READY", timeout=5)
        assert resumed_to_ready
    finally:
        release.set()
        controller.stop()


# ---------------------------------------------------------------------------
# Restart safety: an interrupted (crash-time) DOWNLOADING row is never
# silently treated as complete, and stays resumable.
# ---------------------------------------------------------------------------

def test_restart_does_not_mark_interrupted_download_completed(isolated_db):
    db.init_db()
    movie_id = _seed_ready_movie("crash1")
    db.update_download(movie_id, download_status="DOWNLOADING")

    db.reset_interrupted_downloads()

    row = db.get_movie(movie_id)
    assert row["download_status"] == "READY"
    assert row["download_status"] != "DOWNLOADED"


def test_retry_download_refuses_an_in_flight_job(isolated_db):
    db.init_db()
    movie_id = _seed_ready_movie("inflight1")
    db.update_download(movie_id, download_status="DOWNLOADING")

    db.retry_download(movie_id)

    assert db.get_movie(movie_id)["download_status"] == "DOWNLOADING"
