"""Tuned Igbo and Hausa language confidence, search strategies and isolation."""
import pytest

from movie_manager import db
from movie_manager.content_rules import find_non_movie_evidence, is_language_candidate
from movie_manager.discovery import ACCEPTED_STATUSES, DiscoveryController
from movie_manager.events import activity
from movie_manager.language_profiles import PROFILES, build_search_plans, language_folder
from movie_manager.runtime import Runtime
from movie_manager.webapp import create_app


def igbo(title, channel="Movie TV", description="", query="Igbo movie", audio=""):
    return is_language_candidate("igbo", audio, "", title, description, channel, query)


def hausa(title, channel="Movie TV", description="", query="Hausa movie", audio=""):
    return is_language_candidate("hausa", audio, "", title, description, channel, query)


# --- Igbo: positive evidence ---------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(title="Udochukwu (Give me peace) Full Igbo movie"),
    dict(title="IJE ELU 1&2 - 2018 Latest Nigerian Nollywood Igbo Movie Full HD"),
    dict(title="The Story of Jesus - Igbo / Ibo Language"),
    dict(title="Nsogbu Nnedi", channel="Nollywood Igbo Movies"),                       # channel evidence
    dict(title="Onye-Eze", description="Watch the latest Igbo movie of the year."),    # description phrase
    dict(title="Ihe Onye Metara", channel="Igbo Comedy Tv", audio="en"),               # English tag, Igbo channel
    dict(title="Obi Mmiri", audio="ig"),                                              # explicit language tag
    dict(title="Obata Osu Part 1 @chiefimocomedy", channel="OriakuTv"),               # Igbo comedy brand
    dict(title="Ndi Igbo Full Movie 2024"),
])
def test_igbo_positive_evidence(kwargs):
    assert igbo(**kwargs) is True


# --- Igbo: generic / wrong-language content is not accepted ---------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(title="LANDLORD AND PEPPER - EBUBE OBIO, NKEM OWOH - Latest Nigerian Movie", audio="en"),
    dict(title="Nollywood Family Drama", description="A Nigerian feature film."),
    dict(title="THE KINGS HEAD - Trending Ugezu J Ugezu Epic Movie - 2026 Latest Nigerian Movies"),
    dict(title="THE MYSTERIOUS SISTERS EBUBE OBIE/BROWNY IGBOEGWU/2022 LATEST NIGERIAN MOVIE"),   # actor name
    dict(title="IGODO GOD OF WAR - UGEZU J UGEZU, STANLEY IGBOANUGO LATEST 2026"),                # actor name
    dict(title="IGBOENU DEITY - UGEGBE AJAELO | 2026 African Movies"),                           # not the word Igbo
    dict(title="Sunday Igboho: The Untold Story"),
    dict(title="Iya Igbo - Latest Yoruba Movies 2026", channel="Yoruba Styles"),                 # Yoruba film
    dict(title="IGBO IRUMOLE - Latest Yoruba Movie 2026", channel="YORUBA FILMHOUSE"),
    dict(title="Rayuwa - Latest Hausa Film 2026", description="Kannywood film"),                  # Hausa film
    dict(title="Obi Mmiri", audio="en"),                                                        # English, no Igbo evidence
    dict(title="Nkem", channel="Yoruba Movies Hub", description="An Igbo movie."),               # Yoruba channel wins
])
def test_igbo_wrong_language_is_rejected(kwargs):
    assert igbo(**kwargs) is False


def test_igbo_matching_is_whole_word_but_yoruba_keeps_its_old_substring_behaviour():
    assert igbo("Mmasinachi - Igbo/English Movie") is True            # punctuation boundaries are fine
    assert is_language_candidate("yoruba", "", "", "Aje", "", "YorubaMoviesHub", "Yoruba movie") is True


def test_kids_music_and_lessons_are_not_igbo_movies():
    assert find_non_movie_evidence(
        "Learn Igbo ABCh, 123 + More Fun Cartoons | Nursery Rhymes - Children's Songs", "", 81 * 60) is not None
    assert find_non_movie_evidence("Igbo Gospel Songs Playlist", "", 90 * 60) is not None
    assert find_non_movie_evidence("Mmasinachi - Igbo Nigerian Movie", "", 123 * 60) is None


# --- search strategies ------------------------------------------------------------------------------

def test_igbo_search_plans_cover_the_requested_spread_and_stay_igbo():
    plans = build_search_plans("igbo")
    queries = {p["query"] for p in plans}

    for wanted in (
        "Igbo movie", "Igbo full movie", "Igbo film", "latest Igbo movie", "new Igbo movie", "old Igbo movie",
        "classic Igbo movie", "Nigerian Igbo movie", "Igbo Nollywood movie", "Igbo language movie",
        "Igbo traditional movie", "Igbo village movie", "Igbo epic movie", "Igbo drama", "Igbo comedy movie",
    ):
        assert wanted in queries, wanted
    assert all("igbo" in q.lower() for q in queries)
    assert not any("yoruba" in q.lower() or "hausa" in q.lower() for q in queries)
    assert any("publishedAfter" in p["params"] for p in plans)           # year variations
    assert any(p["params"].get("order") for p in plans)                  # ordering variations
    assert len({p["key"] for p in plans}) == len(plans)


# --- isolation (catalogue, targets, folders) with the tuned profiles ------------------------------------

@pytest.fixture
def client(monkeypatch, isolated_db):
    db.init_db()
    activity.clear()
    monkeypatch.setattr("movie_manager.webapp.runtime", Runtime())
    return create_app().test_client()


def _movie(video_id, language, status="ACCEPTED"):
    return {
        "language": language, "video_id": video_id, "title": f"{language} {video_id}",
        "normalised_title": f"{language} {video_id}", "description": "x", "channel_title": "c",
        "duration_seconds": 4200, "youtube_url": f"https://example.test/{video_id}", "status": status,
    }


def test_each_language_has_its_own_catalogue_target_and_folder(client):
    for i in range(3):
        db.upsert_movie(_movie(f"y{i}", "yoruba"))
    for i in range(2):
        db.upsert_movie(_movie(f"i{i}", "igbo"))
    db.upsert_movie(_movie("h0", "hausa"))
    db.set_setting("target:yoruba", 1000)
    db.set_setting("target:igbo", 20)
    db.set_setting("target:hausa", 5)

    state = {}
    for language in ("igbo", "hausa", "yoruba"):
        assert client.post("/api/settings", json={"active_language": language}).status_code == 200
        data = client.get("/api/bootstrap").get_json()
        rows = client.get(f"/api/movies?language={language}&status=ALL").get_json()
        state[language] = (data["counts"]["accepted"], data["target"], {r["language"] for r in rows})

    assert state == {
        "igbo": (2, 20, {"igbo"}),
        "hausa": (1, 5, {"hausa"}),
        "yoruba": (3, 1000, {"yoruba"}),
    }
    assert [language_folder(x) for x in ("yoruba", "igbo", "hausa")] == ["Yoruba", "Igbo", "Hausa"]


def test_igbo_discovery_never_touches_yoruba_or_hausa_records(monkeypatch, isolated_db):
    db.init_db()
    activity.clear()
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake")
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *a, **k: None)
    for i in range(4):
        db.upsert_movie(_movie(f"y{i}", "yoruba", status="DOWNLOADED"))
    db.upsert_movie(_movie("h0", "hausa"))
    before_yoruba = db.list_movies("yoruba", status="ALL")
    controller = DiscoveryController()

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def item(video_id, title, channel):
        return {"id": video_id, "snippet": {"title": title, "description": "", "channelTitle": channel, "thumbnails": {}},
                "contentDetails": {"duration": "PT1H40M"}, "status": {"embeddable": True}}

    items = {  # the non-Igbo results come first so every one of them is judged before the target is met
        "yo1": item("yo1", "Aweja - Full Yoruba Movie 2025", "Yoruba Hub"),
        "ha1": item("ha1", "Rayuwa - Latest Hausa Film", "Arewa TV"),
        "en1": item("en1", "Nollywood Family Drama", "Nolly TV"),
        "ig1": item("ig1", "Mmasinachi - Igbo Nigerian Movie", "Kodi TV"),
        "ig2": item("ig2", "Ije Elu - Nollywood Igbo Movie Full HD", "Nollywood Igbo Movies"),
    }
    monkeypatch.setattr(controller, "_search", lambda p: Response({"items": [{"id": {"videoId": v}} for v in items]}))
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [items[v] for v in ids])
    controller.language, controller.target, controller.job_id = "igbo", 2, None

    controller._run()

    assert controller.status == "COMPLETED"
    assert {r["video_id"] for r in db.list_movies("igbo", status="ALL") if r["status"] == "ACCEPTED"} == {"ig1", "ig2"}
    rejected = {r["video_id"]: r["rejection_reason"] for r in db.list_movies("igbo", status="REJECTED")}
    assert rejected.get("yo1") == "WRONG_LANGUAGE" and rejected.get("ha1") == "WRONG_LANGUAGE"
    assert db.list_movies("yoruba", status="ALL") == before_yoruba
    assert db.count_movies("hausa", ACCEPTED_STATUSES) == 1
    assert all(e["language"] == "igbo" for e in activity.recent(limit=0))


# --- Hausa: positive evidence ------------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(title="YAN KARYA FULL MOVIE Latest Hausa Film 2026"),
    dict(title="TSUMAGIYA, Hadiza Gabon New Hausa Movie, Kannywood Films"),
    dict(title="Sabon Fim Mai Dadi", query="Kannywood movie"),
    dict(title="Rayuwa", description="A new Kannywood film on our channel."),
    dict(title="KEJI FULL MOVIE WITH SUBTITLE", channel="ALOLO HAUSA TV"),                  # channel evidence
    dict(title="Aure Akan Aure", channel="Arewa Films", query="Hausa film"),
    dict(title="Gangar So Full Movie", audio="ha"),                                         # explicit language tag
    dict(title="Kaddara Ce Complete Film Starring Adam A Zango", query="Hausa full movie"),  # Kannywood cast
    dict(title="ZAMAN LAFIYA", channel="Ali Nuhu Official", query="Hausa movie"),            # star-named channel
    dict(title="Inuwa Part 2 Hausa dub studio", query="Hausa movie"),
])
def test_hausa_positive_evidence(kwargs):
    assert hausa(**kwargs) is True


# --- Hausa: generic / wrong-language content is not accepted --------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    dict(title="Nollywood Family Drama", description="A Nigerian feature film."),
    dict(title="LOVE & FAITH (HELLO AREWA) LATEST 2026 NIGERIA FULL MOVIE", channel="Amal motion pictures", audio="en"),
    dict(title="Aweja - Full Yoruba Movie 2025", channel="Yoruba Hub"),
    dict(title="Latest Nigerian Igbo Movie Full HD", channel="Nollywood Igbo Movies"),
    dict(title="Obi Mmiri", description="An Igbo movie in Hausa areas.", query="Hausa movie"),
    dict(title="Aure", audio="en"),                                                       # English, no Hausa evidence
    dict(title="Rayuwa", channel="Yoruba Movies Hub", description="A Hausa movie."),        # Yoruba channel wins
])
def test_hausa_wrong_language_is_rejected(kwargs):
    assert hausa(**kwargs) is False


def test_kannywood_cast_evidence_does_not_leak_into_other_languages():
    assert igbo("Kaddara Ce Starring Adam A Zango", query="Igbo movie") is False
    assert is_language_candidate("yoruba", "", "", "Kaddara Ce Starring Adam A Zango", "", "TV", "Yoruba movie") is False


def test_hausa_series_episodes_and_kids_content_are_not_movies():
    assert find_non_movie_evidence("MAIMUNA MOVIE PART 2. TASKAR KANNYWOOD EPISODE 31", "", 79 * 60) is not None
    assert find_non_movie_evidence("Koyi Hausa Alphabet Cartoons", "", 70 * 60) is not None
    assert find_non_movie_evidence("AURE AKAN AURE - Complete Hausa Film", "", 84 * 60) is None


def test_hausa_description_boilerplate_does_not_reject_a_full_film():
    description = "Subscribe for movie clips, trailers and promo. Kannywood film with English subtitles."

    assert find_non_movie_evidence("RISALA (Full Hausa Movie)", description, 138 * 60, "ARS HAUSA TV") is None


def test_hausa_search_plans_cover_the_requested_spread_and_stay_hausa():
    plans = build_search_plans("hausa")
    queries = {p["query"] for p in plans}

    for wanted in (
        "Hausa movie", "Hausa full movie", "Hausa film", "latest Hausa movie", "new Hausa movie",
        "old Hausa movie", "classic Hausa movie", "Nigerian Hausa movie", "Hausa language movie",
        "Kannywood movie", "Kannywood full movie", "latest Kannywood movie", "Hausa drama",
        "Hausa comedy movie", "Hausa traditional movie",
    ):
        assert wanted in queries, wanted
    assert all(any(m in q.lower() for m in ("hausa", "kannywood", "arewa", "fim")) for q in queries)
    assert not any("yoruba" in q.lower() or "igbo" in q.lower() for q in queries)
    assert any("publishedAfter" in p["params"] for p in plans)
    assert len({p["key"] for p in plans}) == len(plans)


def test_existing_query_progress_keys_are_preserved_for_every_language():
    for language, profile in PROFILES.items():
        keys = [p["key"] for p in build_search_plans(language)]
        assert keys[:len(profile["queries"])] == profile["queries"]


def test_hausa_discovery_logs_hausa_searches_and_keeps_other_catalogues_intact(monkeypatch, isolated_db):
    db.init_db()
    activity.clear()
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake")
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *a, **k: None)
    db.upsert_movie(_movie("y0", "yoruba", status="DOWNLOADED"))
    db.upsert_movie(_movie("i0", "igbo"))
    controller = DiscoveryController()

    class Response:
        status_code = 200

        def json(self):
            return {"items": [{"id": {"videoId": "h1"}}, {"id": {"videoId": "h2"}}]}

    def item(video_id, title, channel):
        return {"id": video_id, "snippet": {"title": title, "description": "", "channelTitle": channel, "thumbnails": {}},
                "contentDetails": {"duration": "PT1H30M"}, "status": {"embeddable": True}}

    details = {"h1": item("h1", "Rayuwa - Latest Hausa Film", "SA ADAI TV"),
               "h2": item("h2", "Zaman Lafiya", "Ali Nuhu Official")}
    calls = []
    monkeypatch.setattr(controller, "_search", lambda p: calls.append(p["q"]) or Response())
    monkeypatch.setattr(controller, "_fetch_details", lambda ids: [details[v] for v in ids])
    controller.language, controller.target, controller.job_id = "hausa", 2, None

    controller._run()

    assert db.count_movies("hausa", ACCEPTED_STATUSES) == 2
    assert db.count_movies("igbo", ACCEPTED_STATUSES) == 1
    assert db.count_movies("yoruba", ["DOWNLOADED"]) == 1
    assert any(w in calls[0].lower() for w in ("hausa", "kannywood"))
    text = " ".join(e["message"] for e in activity.recent(limit=0))
    assert "Searching YouTube for Hausa movies" in text and "Igbo" not in text and "Yoruba" not in text


def test_target_progress_and_folder_are_per_language(client):
    db.set_setting("target:igbo", 1000)
    db.set_setting("target:hausa", 1000)

    for language, folder in (("igbo", "Igbo"), ("hausa", "Hausa")):
        client.post("/api/settings", json={"active_language": language})
        data = client.get("/api/bootstrap").get_json()
        assert (data["language"], data["target"], data["counts"]["accepted"]) == (language, 1000, 0)
        assert language_folder(language) == folder
