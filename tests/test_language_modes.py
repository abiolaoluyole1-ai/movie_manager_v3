"""Find More continuation, Live Log, and independent Yoruba / Igbo / Hausa modes."""
import json
from pathlib import Path

import pytest

from movie_manager import db
from movie_manager.content_rules import is_language_candidate
from movie_manager.discovery import ACCEPTED_STATUSES, DiscoveryController, YouTubeAPIError
from movie_manager.download import DownloadController
from movie_manager.events import activity, redact_secrets
from movie_manager.language_profiles import PROFILES, build_search_plans, language_folder
from movie_manager.runtime import Runtime
from movie_manager.webapp import create_app


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _movie(video_id, language="yoruba", status="ACCEPTED", title=None):
    return {
        "language": language, "video_id": video_id, "title": title or f"Seed {video_id}",
        "normalised_title": f"seed {video_id}", "description": f"A {language} movie.",
        "channel_id": "c", "channel_title": f"{language.capitalize()} Channel",
        "duration_seconds": 4200, "youtube_url": f"https://example.test/{video_id}",
        "status": status, "download_status": "NOT_READY",
    }


def _seed(n, language="yoruba", prefix="seed"):
    for i in range(n):
        db.upsert_movie(_movie(f"{prefix}{i}", language))


def _mark_base_queries_exhausted(language="yoruba"):
    """The user's real situation: every original query already paged to its end."""
    for query in PROFILES[language]["queries"]:
        db.save_search_state(language, query, None, True)


def _item(video_id, language="yoruba", title=None, duration="PT1H30M"):
    label = language.capitalize()
    return {
        "id": video_id,
        "snippet": {
            "title": title or f"Classic {label} Movie {video_id}",
            "description": f"An award-winning {label} movie full length feature.",
            "channelTitle": f"{label} Channel", "thumbnails": {},
        },
        "contentDetails": {"duration": duration},
        "status": {"embeddable": True},
    }


@pytest.fixture
def controller(monkeypatch, isolated_db):
    db.init_db()
    activity.clear()
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *a, **k: None)
    return DiscoveryController()


def _unique_pages(monkeypatch, controller, language="yoruba", per_page=50):
    """Every search page returns brand-new candidate videos."""
    calls = []

    def fake_search(params):
        calls.append(dict(params))
        n = len(calls)
        return FakeResponse({"items": [{"id": {"videoId": f"new{n}_{i}"}} for i in range(per_page)]})

    monkeypatch.setattr(controller, "_search", fake_search)
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [_item(v, language) for v in ids])
    return calls


def _run(controller, language, target):
    controller.language, controller.target, controller.job_id = language, target, None
    controller._run()


def _log_text():
    return "\n".join(e["message"] for e in activity.recent(limit=0))


# --- Find More continues instead of silently stopping -------------------------

def test_find_more_resumes_from_a_high_catalogue_with_exhausted_base_queries(monkeypatch, controller):
    _seed(951)
    _mark_base_queries_exhausted()
    calls = _unique_pages(monkeypatch, controller)

    _run(controller, "yoruba", 952)

    assert calls, "must make real search requests even though the base queries are exhausted"
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 952
    assert controller.status == "COMPLETED"
    assert controller.message == "Discovery complete: 952 / 952"


def test_new_search_strategies_do_not_reuse_exhausted_query_state(monkeypatch, controller):
    _seed(10)
    _mark_base_queries_exhausted()
    calls = _unique_pages(monkeypatch, controller)

    _run(controller, "yoruba", 11)

    first = calls[0]
    assert first["q"] not in PROFILES["yoruba"]["queries"] or "publishedAfter" in first or "order" in first


def test_search_plans_keep_base_query_keys_and_add_distinct_strategies():
    plans = build_search_plans("yoruba")
    keys = [p["key"] for p in plans]

    assert keys[:len(PROFILES["yoruba"]["queries"])] == PROFILES["yoruba"]["queries"]
    assert len(keys) == len(set(keys))
    assert any("publishedAfter" in p["params"] for p in plans)
    assert any(p["params"].get("order") == "date" for p in plans)
    for language in ("igbo", "hausa"):
        assert len(build_search_plans(language)) > len(PROFILES[language]["queries"])


def test_exhausted_pool_is_reported_not_silent_and_not_called_complete(monkeypatch, controller):
    _seed(5)
    seen = {}

    def only_duplicates(params):
        seen[params["q"], params.get("publishedAfter"), params.get("order")] = 1
        return FakeResponse({"items": [{"id": {"videoId": "seed0"}}, {"id": {"videoId": "seed1"}}]})

    monkeypatch.setattr(controller, "_search", only_duplicates)
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [])

    _run(controller, "yoruba", 1000)

    assert controller.status == "EXHAUSTED"
    assert controller.message.startswith("Search pool exhausted. Found 5 of 1000.")
    assert "No additional unique qualifying movies were found." in controller.message
    assert "Search pool exhausted. Found 5 of 1000." in _log_text()
    assert controller.stats["duplicates_skipped"] > 0
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 5


def test_duplicate_only_results_with_paging_terminate(monkeypatch, controller):
    _seed(3)
    pages = {}

    def paged(params):
        key = (params["q"], params.get("publishedAfter"), params.get("order"))
        pages[key] = pages.get(key, 0) + 1
        token = f"p{pages[key]}" if pages[key] < 3 else None
        return FakeResponse({"items": [{"id": {"videoId": "seed0"}}], "nextPageToken": token})

    monkeypatch.setattr(controller, "_search", paged)
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [])

    _run(controller, "yoruba", 50)

    assert controller.status == "EXHAUSTED"
    assert max(pages.values()) == 3


def test_runaway_token_chain_is_stopped(monkeypatch, controller):
    _seed(3)
    calls = []

    def endless(params):
        calls.append(params["q"])
        return FakeResponse({"items": [{"id": {"videoId": "seed0"}}], "nextPageToken": "again"})

    monkeypatch.setattr(controller, "_search", endless)
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [])

    _run(controller, "yoruba", 50)

    assert controller.status == "EXHAUSTED"
    assert len(calls) <= len(build_search_plans("yoruba")) * 15


# --- exact target ---------------------------------------------------------------

def test_exact_target_is_never_overshot(monkeypatch, controller):
    _seed(998)
    _unique_pages(monkeypatch, controller)

    _run(controller, "yoruba", 1000)

    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 1000
    assert controller.status == "COMPLETED"
    assert controller.message == "Discovery complete: 1000 / 1000"


def test_target_reached_mid_page_keeps_the_page_for_next_time(monkeypatch, controller):
    _seed(10)
    calls = _unique_pages(monkeypatch, controller, per_page=50)

    _run(controller, "yoruba", 11)

    plan = build_search_plans("yoruba")[0]
    token, exhausted = db.get_search_state("yoruba", plan["key"])
    assert exhausted is False  # unchecked candidates on that page are not skipped over
    assert len(calls) == 1


# --- errors surface ------------------------------------------------------------------

def test_quota_error_is_shown_in_status_and_live_log(monkeypatch, controller):
    _seed(3)

    def quota(params):
        raise YouTubeAPIError(403, "quotaExceeded", "Quota exceeded.")

    monkeypatch.setattr(controller, "_search", quota)

    _run(controller, "yoruba", 10)

    assert controller.status == "ERROR"
    assert "daily search quota" in controller.message
    assert "daily search quota" in _log_text()


def test_unexpected_worker_exception_is_logged_not_swallowed(monkeypatch, controller, caplog):
    _seed(3)

    def boom(params):
        raise ValueError("database exploded")

    monkeypatch.setattr(controller, "_search", boom)

    with caplog.at_level("ERROR"):
        _run(controller, "yoruba", 10)

    assert controller.status == "ERROR"
    assert controller.message == "database exploded"
    assert "Discovery worker crashed" in caplog.text
    assert "Traceback" in caplog.text
    assert "database exploded" in _log_text()


def test_missing_api_key_is_a_visible_error(monkeypatch, controller):
    monkeypatch.setenv("YOUTUBE_API_KEY", "")

    _run(controller, "yoruba", 10)

    assert controller.status == "ERROR"
    assert "API key is missing" in _log_text()


def test_secrets_are_redacted_from_the_live_log():
    entry = activity.add("Failed: https://x/y?part=snippet&key=AIzaSyPRIVATE123&q=a", "bad")

    assert "AIzaSyPRIVATE123" not in entry["message"]
    assert "key=<redacted>" in entry["message"]
    assert "SECRET" not in redact_secrets("cookie=SECRET; token=SECRET")


# --- Live Log receives discovery activity and stop reasons --------------------------

def test_live_log_records_discovery_steps_in_order(monkeypatch, controller):
    _seed(5)
    _unique_pages(monkeypatch, controller, per_page=3)

    _run(controller, "yoruba", 7)

    lines = [e["message"] for e in activity.recent(limit=0)]
    assert lines[0].startswith("Yoruba discovery started")
    joined = "\n".join(lines)
    assert "Searching YouTube for Yoruba movies:" in joined
    assert "Scanned 3 results" in joined
    assert "✓ Accepted: Classic Yoruba Movie" in joined
    assert lines[-1] == "Discovery complete: 7 / 7"
    assert all(e["language"] == "yoruba" and e["scope"] == "discovery" for e in activity.recent(limit=0))


def test_live_log_explains_rejections_and_duplicates(monkeypatch, controller):
    _seed(1)
    items = {
        "trailer1": _item("trailer1", title="Big Yoruba Movie Trailer"),
        "short1": _item("short1", duration="PT20M"),
        "eng1": {**_item("eng1", title="Nollywood Family Drama"),
                 "snippet": {"title": "Nollywood Family Drama", "description": "A Nigerian film.",
                             "channelTitle": "Nollywood TV", "thumbnails": {}}},
        "good1": _item("good1"),
    }
    monkeypatch.setattr(controller, "_search", lambda params: FakeResponse({
        "items": [{"id": {"videoId": v}} for v in [*items, "seed0"]]}))
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [items[v] for v in ids])

    _run(controller, "yoruba", 2)

    text = _log_text()
    assert "1 already in your catalogue" in text
    assert "✕ Rejected (trailer)" in text
    assert "✕ Too short (20m)" in text
    assert "✕ Wrong language: Nollywood Family Drama" in text
    assert "✓ Accepted: Classic Yoruba Movie good1" in text


def test_stop_reason_is_logged(monkeypatch, controller):
    _seed(3)

    def stopping(params):
        controller._stop.set()
        return FakeResponse({"items": []})

    monkeypatch.setattr(controller, "_search", stopping)
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [])

    _run(controller, "yoruba", 10)

    assert controller.status == "STOPPED"
    assert "Discovery stopped. Progress is saved: 3 / 10." in _log_text()


def test_finished_run_is_not_replaced_by_an_older_job_on_refresh(monkeypatch, isolated_db):
    """The "flips back to Idle" bug: restore skipped the newest zero-work job."""
    db.init_db()
    old = {"candidates_scanned": 500, "api_requests": 12, "accepted": 900}
    old_id = db.create_job("DISCOVERY", "yoruba", 980, "COMPLETED", old, "Target reached")
    new_id = db.create_job("DISCOVERY", "yoruba", 1000, "RUNNING", {}, "Starting")
    db.update_job(new_id, status="EXHAUSTED", message="Search pool exhausted. Found 951 of 1000.")
    controller = DiscoveryController()

    snapshot = controller.restore_latest("yoruba")

    assert snapshot["status"] == "EXHAUSTED"
    assert snapshot["message"].startswith("Search pool exhausted")
    assert old_id != new_id


def test_log_endpoint_returns_recent_lines_filtered_by_language(monkeypatch, isolated_db):
    db.init_db()
    activity.clear()
    activity.add("Yoruba line", scope="discovery", language="yoruba")
    activity.add("Igbo line", scope="discovery", language="igbo")
    activity.add("Shared line", scope="app")
    monkeypatch.setattr("movie_manager.webapp.runtime", Runtime())
    client = create_app().test_client()

    igbo = client.get("/api/log?language=igbo").get_json()["entries"]
    after = client.get(f"/api/log?after={igbo[0]['id']}").get_json()["entries"]

    assert [e["message"] for e in igbo] == ["Igbo line", "Shared line"]
    assert [e["message"] for e in after][-1] == "Shared line"
    assert client.post("/api/log/clear").get_json()["ok"] is True
    assert client.get("/api/log").get_json()["entries"] == []


# --- independent Yoruba / Igbo / Hausa --------------------------------------------------

@pytest.fixture
def client(monkeypatch, isolated_db):
    db.init_db()
    monkeypatch.setattr("movie_manager.webapp.runtime", Runtime())
    return create_app().test_client()


def _switch(client, language):
    assert client.post("/api/settings", json={"active_language": language}).status_code == 200
    return client.get("/api/bootstrap").get_json()


def test_language_switch_loads_that_languages_own_state(client):
    _seed(7, "yoruba")
    _seed(2, "igbo", prefix="ig")
    db.set_setting("target:yoruba", 1000)

    yoruba = _switch(client, "yoruba")
    igbo = _switch(client, "igbo")
    hausa = _switch(client, "hausa")
    back = _switch(client, "yoruba")

    assert (yoruba["language"], yoruba["counts"]["accepted"], yoruba["target"]) == ("yoruba", 7, 1000)
    assert (igbo["language"], igbo["counts"]["accepted"], igbo["target"]) == ("igbo", 2, 1000)
    assert (hausa["language"], hausa["counts"]["accepted"], hausa["target"]) == ("hausa", 0, 1000)
    assert (back["counts"]["accepted"], back["target"]) == (7, 1000)
    assert {k: v["enabled"] for k, v in back["languages"].items()} == {
        "yoruba": True, "igbo": True, "hausa": True}


def test_language_targets_are_independent(client):
    db.set_setting("target:yoruba", 952)
    db.set_setting("target:igbo", 3)

    assert _switch(client, "igbo")["target"] == 3
    assert _switch(client, "hausa")["target"] == 1000
    assert _switch(client, "yoruba")["target"] == 952


def test_each_language_restores_only_its_own_discovery_run(client):
    db.create_job("DISCOVERY", "yoruba", 1000, "EXHAUSTED",
                  {"candidates_scanned": 40, "api_requests": 2}, "Search pool exhausted.")

    yoruba = _switch(client, "yoruba")["runtime"]["discovery"]
    igbo = _switch(client, "igbo")["runtime"]["discovery"]
    again = _switch(client, "yoruba")["runtime"]["discovery"]

    assert yoruba["status"] == "EXHAUSTED" and yoruba["stats"]["candidates_scanned"] == 40
    assert igbo["status"] == "IDLE" and igbo["language"] == "igbo"
    assert igbo["stats"]["candidates_scanned"] == 0 and igbo["message"] == ""
    assert again["status"] == "EXHAUSTED" and again["stats"]["candidates_scanned"] == 40


def test_movies_api_never_mixes_languages(client):
    _seed(3, "yoruba")
    _seed(2, "igbo", prefix="ig")
    db.upsert_movie(_movie("h1", "hausa"))
    db.upsert_movie(_movie("shared", "yoruba"))
    db.upsert_movie(_movie("shared", "igbo"))  # same video id may exist in two catalogues

    for language, expected in (("yoruba", 4), ("igbo", 3), ("hausa", 1)):
        rows = client.get(f"/api/movies?language={language}&status=ALL").get_json()
        assert len(rows) == expected
        assert {r["language"] for r in rows} == {language}
        assert len(client.get(f"/api/movies/ids?language={language}").get_json()["ids"]) == expected


def test_unknown_language_cannot_become_active(client):
    response = client.post("/api/settings", json={"active_language": "klingon"})

    assert response.status_code == 400
    assert client.get("/api/bootstrap").get_json()["language"] == "yoruba"


@pytest.mark.parametrize("language,target", [("igbo", 3), ("hausa", 3)])
def test_igbo_and_hausa_discovery_stays_in_its_own_catalogue(monkeypatch, controller, language, target):
    _seed(4, "yoruba")
    calls = _unique_pages(monkeypatch, controller, language=language, per_page=5)

    _run(controller, language, target)

    assert db.count_movies(language, ACCEPTED_STATUSES) == target
    assert db.count_movies("yoruba", ACCEPTED_STATUSES) == 4
    assert all(r["language"] == language for r in db.list_movies(language, status="ALL"))
    assert PROFILES[language]["label"] in controller.snapshot()["current_query"] or calls
    assert f"{PROFILES[language]['label']} discovery started" in _log_text()
    assert all(e["language"] == language for e in activity.recent(limit=0))
    assert controller.status == "COMPLETED"


def test_igbo_and_hausa_search_with_their_own_queries(monkeypatch, controller):
    for language in ("igbo", "hausa"):
        calls = _unique_pages(monkeypatch, controller, language=language, per_page=1)
        _run(controller, language, 1)
        word = "igbo" if language == "igbo" else "hausa"
        assert word in calls[0]["q"].lower()
        assert "yoruba" not in calls[0]["q"].lower()


def test_same_video_can_be_judged_separately_per_language(monkeypatch, controller):
    yoruba_item = _item("only-yoruba", "yoruba")
    monkeypatch.setattr(controller, "_search", lambda p: FakeResponse({"items": [{"id": {"videoId": "only-yoruba"}}]}))
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [yoruba_item])

    _run(controller, "igbo", 1)  # Yoruba-titled video found while searching Igbo

    assert db.count_movies("igbo", ACCEPTED_STATUSES) == 0
    assert db.count_movies("yoruba") == 0
    assert controller.stats["rejected_wrong_language"] >= 1


@pytest.mark.parametrize("language,kwargs,expected", [
    ("igbo", dict(title="Nkem - Latest Igbo Movie 2024", description="", channel="Nollywood Hub", query="Igbo movie"), True),
    ("igbo", dict(title="Family Drama", description="A new Igbo movie.", channel="TV", query="Igbo full movie"), True),
    ("igbo", dict(title="Nkem", description="", channel="TV", query="Igbo movie", audio="ig"), True),
    ("igbo", dict(title="Nollywood Family Drama", description="A Nigerian film.", channel="TV", query="Igbo movie"), False),
    ("igbo", dict(title="Igbo Irunmole - Yoruba Movie", description="", channel="TV", query="Igbo movie"), False),
    ("igbo", dict(title="Sunday Igboho Story", description="", channel="TV", query="Igbo movie"), False),
    ("igbo", dict(title="Nkem Igbo movie", description="", channel="Yoruba Movies Hub", query="Igbo movie"), False),
    ("igbo", dict(title="Nkem", description="", channel="TV", query="Igbo movie", audio="en"), False),
    ("hausa", dict(title="Sabon Fim Mai Dadi", description="", channel="Arewa Films", query="Kannywood movie"), True),
    ("hausa", dict(title="Rayuwa - Hausa Movie", description="", channel="TV", query="Hausa movie"), True),
    ("hausa", dict(title="Rayuwa", description="", channel="TV", query="Hausa movie", audio="ha"), True),
    ("hausa", dict(title="Nollywood Family Drama", description="A Nigerian film.", channel="TV", query="Hausa movie"), False),
    ("hausa", dict(title="Latest Yoruba Movie", description="", channel="TV", query="Hausa movie"), False),
    ("yoruba", dict(title="Igbo Irunmole", description="A Yoruba movie.", channel="TV", query="Yoruba movie"), True),
])
def test_language_gate(language, kwargs, expected):
    assert is_language_candidate(
        language, kwargs.get("audio", ""), "", kwargs["title"], kwargs["description"],
        kwargs["channel"], kwargs["query"],
    ) is expected


# --- downloads follow the selected language ------------------------------------------------

def test_download_folder_follows_the_movie_language(monkeypatch, tmp_path):
    monkeypatch.setattr("movie_manager.download.get_setting", lambda key, default=None: str(tmp_path))
    controller = DownloadController()

    for language, folder in (("yoruba", "Yoruba"), ("igbo", "Igbo"), ("hausa", "Hausa")):
        final, part = controller._output_paths({
            "language": language, "title": "A Movie", "video_id": "abc", "download_backend": "YTDLP"})
        assert final.parent == tmp_path / folder
        assert final.parent.is_dir()
        assert language_folder(language) == folder


def test_download_workers_stay_bound_to_one_language(monkeypatch, isolated_db):
    db.init_db()
    _seed(1, "igbo", prefix="ig")
    controller = DownloadController()
    controller.language = "yoruba"
    controller.active = {1: {"movie_id": 1}}
    live = type("T", (), {"is_alive": lambda self: True, "movie_manager_worker_id": 0})()
    controller._workers = [live]

    with pytest.raises(RuntimeError, match="still running"):
        controller.start("igbo")

    controller.active = {}
    controller.start("igbo")
    assert controller.language == "igbo"


def test_download_activity_goes_to_the_live_log(monkeypatch):
    activity.clear()
    controller = DownloadController()
    movie = {"id": 5, "title": "Movie ABC", "language": "igbo"}
    controller.active = {5: {"movie_id": 5, "stage": "QUEUED"}}

    controller._log_progress(movie, 150, 1000, 5_000_000)   # 15%
    controller._log_progress(movie, 180, 1000, 5_000_000)   # still the 10% bucket: not repeated
    controller._log_progress(movie, 410, 1000, 4_800_000)   # 41%
    controller._update_job(movie, 0, stage="MERGING")
    controller._update_job(movie, 0, stage="VERIFYING")

    entries = activity.recent(limit=0)
    assert [e["message"] for e in entries] == [
        "↓ Movie ABC — 10% — 4.8 MB/s",
        "↓ Movie ABC — 40% — 4.6 MB/s",
        "Combining video and audio...",
        "Checking movie...",
    ]
    assert all(e["scope"] == "download" and e["language"] == "igbo" for e in entries)


def test_active_download_jobs_carry_their_language(monkeypatch, isolated_db):
    db.init_db()
    movie = _movie("dl1", "hausa", status="ACCEPTED")
    movie.update(download_status="READY")
    db.upsert_movie(movie)
    movie_id = db.get_movie_id("hausa", "dl1")
    db.mark_youtube_download_ready(movie_id)
    claimed = db.next_download_ready("hausa")

    assert claimed["language"] == "hausa"
    assert db.next_download_ready("yoruba") is None
