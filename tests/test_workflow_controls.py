import pytest
import requests

from movie_manager import db
from movie_manager.discovery import ACCEPTED_STATUSES, DiscoveryController
from movie_manager.download import DownloadController


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class AliveThread:
    """A fake thread that never really runs _run() but reports itself alive."""

    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def is_alive(self):
        return True


class DeadThread:
    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def is_alive(self):
        return False


def _movie(video_id, title, language="yoruba"):
    return {
        "language": language, "video_id": video_id, "title": title,
        "normalised_title": title.lower(), "description": "A Yoruba movie.",
        "channel_id": "channel", "channel_title": "Yoruba Channel",
        "duration_seconds": 4200, "youtube_url": f"https://example.test/{video_id}",
        "status": "ACCEPTED", "download_status": "NOT_READY",
    }


# --- Discovery controller status transitions ---------------------------------

def test_discovery_starts_idle():
    controller = DiscoveryController()
    assert controller.status == "IDLE"
    assert controller.network_wait is False


def test_discovery_start_transitions_to_running(monkeypatch):
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.count_movies", lambda *a, **k: 0)
    monkeypatch.setattr("movie_manager.discovery.create_job", lambda *a: 1)
    monkeypatch.setattr("movie_manager.discovery.threading.Thread", DeadThread)

    controller.start("yoruba", 10)

    assert controller.status == "RUNNING"


def test_discovery_pause_transitions_to_paused(monkeypatch):
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.count_movies", lambda *a, **k: 0)
    monkeypatch.setattr("movie_manager.discovery.create_job", lambda *a: 1)
    monkeypatch.setattr("movie_manager.discovery.threading.Thread", AliveThread)
    controller.start("yoruba", 10)

    controller.pause()

    assert controller.status == "PAUSED"


def test_discovery_start_raises_when_already_running_no_duplicate_worker(monkeypatch):
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.count_movies", lambda *a, **k: 0)
    monkeypatch.setattr("movie_manager.discovery.create_job", lambda *a: 1)
    thread_calls = []

    class TrackedAliveThread(AliveThread):
        def __init__(self, **kwargs):
            thread_calls.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr("movie_manager.discovery.threading.Thread", TrackedAliveThread)
    controller.start("yoruba", 10)

    with pytest.raises(RuntimeError):
        controller.start("yoruba", 10)

    assert len(thread_calls) == 1


def test_discovery_run_sets_error_status_on_missing_api_key(monkeypatch):
    controller = DiscoveryController()
    controller.language = "yoruba"
    controller.job_id = None
    monkeypatch.setattr("movie_manager.discovery.os.getenv", lambda *a, **k: "")

    controller._run()

    assert controller.status == "ERROR"
    assert "API key" in controller.message
    assert controller.network_wait is False


# --- Continue-after-completed preserves the catalogue -------------------------

def test_completed_target_reached_after_start_shows_completed(isolated_db, monkeypatch):
    db.init_db()
    for i in range(30):
        db.upsert_movie(_movie(f"acc{i}", f"Movie {i}"))
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.threading.Thread", DeadThread)

    controller.start("yoruba", 30)

    assert controller.status == "COMPLETED"
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 30


def test_continue_after_completed_preserves_catalogue_and_updates_target(isolated_db, monkeypatch):
    db.init_db()
    for i in range(30):
        db.upsert_movie(_movie(f"acc{i}", f"Movie {i}"))
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.threading.Thread", DeadThread)
    controller.start("yoruba", 30)
    assert controller.status == "COMPLETED"

    controller.start("yoruba", 100)

    assert controller.status == "RUNNING"
    assert controller.target == 100
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 30


def test_target_changed_while_paused_and_resumed_continues_toward_new_target(isolated_db, monkeypatch):
    db.init_db()
    for i in range(5):
        db.upsert_movie(_movie(f"p{i}", f"Paused Movie {i}"))
    controller = DiscoveryController()
    monkeypatch.setattr("movie_manager.discovery.threading.Thread", AliveThread)
    controller.start("yoruba", 30)
    controller.pause()
    assert controller.status == "PAUSED"

    controller.start("yoruba", 50)

    assert controller.status == "RUNNING"
    assert controller.target == 50
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 5


# --- network_wait flag (auto-retry state) -------------------------------------

def test_network_wait_flag_set_true_during_retry_wait(monkeypatch):
    controller = DiscoveryController()

    def fake_get(url, params, timeout):
        raise requests.RequestException("boom")

    monkeypatch.setattr("movie_manager.discovery.requests.get", fake_get)
    observed = []

    def fake_sleep(seconds):
        observed.append(controller.network_wait)
        controller._stop.set()

    monkeypatch.setattr("movie_manager.discovery.time.sleep", fake_sleep)

    result = controller._request("http://x", {})

    assert result is None
    assert observed and observed[0] is True
    assert controller.stats["network_retries"] == 1


def test_network_wait_clears_after_successful_retry(monkeypatch):
    controller = DiscoveryController()
    controller.network_wait = True

    def fake_get(url, params, timeout):
        return FakeResponse(200, {"items": []})

    monkeypatch.setattr("movie_manager.discovery.requests.get", fake_get)

    response = controller._request("http://x", {})

    assert response.status_code == 200
    assert controller.network_wait is False


def test_discovery_stop_clears_network_wait():
    controller = DiscoveryController()
    controller.status = "RUNNING"
    controller.network_wait = True

    controller.stop()

    assert controller.network_wait is False


# --- Download controller status transitions -----------------------------------

def test_download_starts_idle():
    controller = DownloadController()
    assert controller.status == "IDLE"
    assert controller.network_wait is False


def test_download_start_resets_network_wait(monkeypatch):
    controller = DownloadController()
    controller.network_wait = True
    monkeypatch.setattr("movie_manager.download.threading.Thread", DeadThread)

    controller.start("yoruba")

    assert controller.network_wait is False


def test_download_start_does_not_create_duplicate_worker_when_running(monkeypatch):
    controller = DownloadController()
    thread_calls = []

    class TrackedAliveThread(AliveThread):
        def __init__(self, **kwargs):
            thread_calls.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr("movie_manager.download.threading.Thread", TrackedAliveThread)

    controller.start("yoruba", concurrency=1)
    controller.start("yoruba", concurrency=1)

    assert len(thread_calls) == 1
    assert controller.status == "RUNNING"


def test_download_start_spawns_one_worker_per_concurrency_slot(monkeypatch):
    controller = DownloadController()
    thread_calls = []

    class TrackedAliveThread(AliveThread):
        def __init__(self, **kwargs):
            thread_calls.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr("movie_manager.download.threading.Thread", TrackedAliveThread)

    controller.start("yoruba", concurrency=5)

    assert len(thread_calls) == 5
    assert controller.concurrency == 5


def test_download_start_clamps_concurrency_to_allowed_range(monkeypatch):
    monkeypatch.setattr("movie_manager.download.threading.Thread", DeadThread)

    low = DownloadController()
    low.start("yoruba", concurrency=0)
    assert low.concurrency == 1

    high = DownloadController()
    high.start("yoruba", concurrency=99)
    assert high.concurrency == 7


def test_download_run_sets_error_status_on_unexpected_exception(monkeypatch):
    controller = DownloadController()

    def boom(language):
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr("movie_manager.download.next_download_ready", boom)

    controller._run()

    assert controller.status == "ERROR"
    assert "disk unavailable" in controller.message
    assert controller.network_wait is False


def test_download_stop_clears_network_wait():
    controller = DownloadController()
    controller.network_wait = True

    controller.stop()

    assert controller.network_wait is False
