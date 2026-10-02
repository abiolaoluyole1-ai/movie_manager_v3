"""Context-aware non-movie filtering: incidental description words must not reject real movies."""
import pytest

from movie_manager import db
from movie_manager.content_rules import find_non_movie_evidence, has_full_movie_evidence
from movie_manager.discovery import ACCEPTED_STATUSES, DiscoveryController, _result_line
from movie_manager.recheck import recheck_rule_rejections

BOILERPLATE = (
    "Genres: Action, Drama, epic, Only on youtube Best Of Nollywood Channel, "
    "see clips, trailer's and exclusives on Nollywood Movie"
)
SEO_TAGS = (
    "best nollywood movie, yoruba movies compilation, yoruba movie clips, yoruba movie trailer, "
    "yoruba movie behind the scenes, yoruba movie review, trending yoruba movie"
)
SEO_TAGS_IGBO = SEO_TAGS.replace("yoruba", "igbo")


def _item(title, description="", duration="PT1H45M", channel="Movie Hub", **snippet):
    return {
        "id": "vid1",
        "snippet": {"title": title, "description": description, "channelTitle": channel,
                    "thumbnails": {}, **snippet},
        "contentDetails": {"duration": duration},
        "status": {"embeddable": True},
    }


@pytest.fixture
def judge(monkeypatch):
    monkeypatch.setattr("movie_manager.discovery.find_probable_title_duplicate", lambda *a, **k: None)
    return DiscoveryController()


def _evaluate(judge, language, query, **kwargs):
    judge.language = language
    return judge._evaluate(_item(**kwargs), query, 3600)


# --- incidental description words no longer reject a real movie ---------------------

@pytest.mark.parametrize("description", [
    BOILERPLATE,                                       # "clips", "trailer's"
    "Subscribe for more. Watch promo videos and exclusive promo content on our channel.",
    "Enjoy the best scenes, dramatic scenes and the full story.",
    "Our other movie clips and promo are on the channel. Thanks for the scenes you shared.",
    SEO_TAGS_IGBO,                                     # tag-style list with trailer/clips/behind the scenes/review
])
def test_incidental_description_words_do_not_reject_a_valid_full_movie(judge, description):
    movie, result = _evaluate(
        judge, "igbo", "Igbo movie",
        title="IJE ELU - 2018 Latest Nigerian Nollywood Igbo Movie Full HD", description=description,
    )

    assert result == "ACCEPTED", movie.get("rejection_reason")
    assert find_non_movie_evidence(movie["title"], description, 6300, "Movie Hub") is None


@pytest.mark.parametrize("word", ["clip", "promo", "scenes"])
def test_a_single_blocked_word_in_a_long_description_never_rejects(word):
    description = "Welcome to our channel. " * 40 + f"Follow us for every {word} we release. " + "More text. " * 40

    assert find_non_movie_evidence("Obi Mmiri - Igbo Movie", description, 65 * 60, "Obi TV") is None


def test_yoruba_full_movies_with_boilerplate_are_no_longer_lost(judge):
    movie, result = _evaluate(
        judge, "yoruba", "Yoruba movie", title="ALAKADA 2 - Yoruba Movie Comedy Toyin Abraham",
        description=SEO_TAGS, channel="Okiki Tv+",
    )

    assert result == "ACCEPTED"


def test_title_with_eclipse_or_scenery_is_not_mistaken_for_a_clip_or_scene():
    assert find_non_movie_evidence("Eclipse - Full Igbo Movie", "", 100 * 60) is None
    assert find_non_movie_evidence("Mountain Scenery Love Story Igbo Movie", "", 100 * 60) is None


# --- genuine non-movies are still rejected ------------------------------------------------

@pytest.mark.parametrize("title,term", [
    ("Obi Mmiri Official Trailer", "trailer"),
    ("OBI MMIRI - Igbo Movie TRAILER 2026", "trailer"),
    ("Igbo Movie Teaser", "teaser"),
    ("Igbo Movie Clip - Best Fight", "clip"),
    ("Short clip from Igbo movie", "clip"),
    ("Behind the Scenes of Igbo Movie", "behind the scenes"),
    ("Exclusive Interview with the Igbo Movie Star", "interview"),
    ("Igbo Movie Review", "review"),
    ("My Reaction to the Igbo Movie", "reaction"),
    ("Igbo Movie Official Music Video", "official music"),
    ("Igbo Movie Soundtrack", "soundtrack"),
    ("Igbo Movie Promo", "promo"),
    ("Igbo Movie Episode 4", "episode"),
])
def test_blocked_words_in_the_title_still_reject(judge, title, term):
    movie, result = _evaluate(judge, "igbo", "Igbo movie", title=title, description="A long Igbo movie.")

    assert result == "NOT_MOVIE"
    assert movie["rejection_reason"] == f"BLOCKED_TERM:{term}"


@pytest.mark.parametrize("title", [
    "Best of Igbo Movies", "Igbo Movie Compilation", "Funny Moments in Igbo Movies", "Scene Compilation Igbo",
    "Igbo Movie Highlights",
])
def test_compilations_are_still_rejected(judge, title):
    movie, result = _evaluate(judge, "igbo", "Igbo movie", title=title)

    assert result == "NOT_MOVIE"
    assert movie["rejection_reason"].startswith("COMPILATION_TITLE:")


def test_a_title_blocked_word_beats_full_movie_wording():
    assert find_non_movie_evidence("Full Movie Trailer - Igbo", "", 120 * 60) == ("TITLE", "trailer")


def test_description_that_says_this_is_a_trailer_rejects_a_short_unlabelled_video():
    assert find_non_movie_evidence(
        "Obi Mmiri", "This trailer is for our upcoming Igbo movie. Official trailer.", 62 * 60, "Obi TV",
    ) == ("DESCRIPTION", "official trailer")


def test_description_interview_statement_rejects_without_full_movie_evidence():
    assert find_non_movie_evidence(
        "Chinedu speaks", "In this interview, Chinedu talks about his career.", 64 * 60, "Obi TV",
    ) == ("DESCRIPTION", "in this interview")


def test_repeated_strong_statements_reject_unless_the_title_claims_a_full_movie():
    description = "This clip is from the movie. Watch the official trailer. Full interview below."

    assert find_non_movie_evidence("Obi Mmiri", description, 100 * 60, "Obi TV") is not None
    assert find_non_movie_evidence("Obi Mmiri (Full Movie)", description, 100 * 60, "Obi TV") is None


def test_clips_and_trailers_channels_make_description_hints_count():
    assert find_non_movie_evidence(
        "Obi Mmiri", "Watch more movie clips every day.", 100 * 60, "Nollywood Movie Clips TV",
    ) == ("DESCRIPTION", "movie clip")


# --- positive full-movie evidence -----------------------------------------------------------------

@pytest.mark.parametrize("title,description,seconds,expected", [
    ("OBI MMIRI (Full Movie)", "", 3700, True),
    ("Aure Akan Aure - Complete Hausa Film", "", 3700, True),
    ("Kissa Hausa Full Film 2026", "", 3700, True),
    ("Ezinne", "A feature film about a village.", 3700, True),
    ("Ezinne", "Full Nollywood movie in HD.", 3700, True),
    ("Obi Mmiri", "", 80 * 60, True),                 # feature length speaks for itself
    ("Obi Mmiri", "", 65 * 60, False),
    ("Obi Mmiri", "Full of drama and scenes.", 65 * 60, False),
])
def test_full_movie_evidence(title, description, seconds, expected):
    assert has_full_movie_evidence(title, description, seconds) is expected


def test_discovery_log_explains_description_rejections():
    movie = {"title": "Obi Mmiri", "duration_seconds": 3900, "rejection_reason": "BLOCKED_DESCRIPTION:official trailer"}

    level, text = _result_line("NOT_MOVIE", movie)

    assert level == "dim"
    assert text == '✕ Rejected (description says "official trailer"): Obi Mmiri'


# --- re-checking old rule rejections (offline) ----------------------------------------------------

def _row(video_id, title, description, language, reason, seconds=7000, channel="Movie Hub"):
    return {
        "language": language, "video_id": video_id, "title": title, "normalised_title": title.lower(),
        "description": description, "channel_title": channel, "duration_seconds": seconds,
        "youtube_url": f"https://example.test/{video_id}", "status": "REJECTED",
        "rejection_reason": reason, "source_query": f"{language.capitalize()} movie",
    }


@pytest.fixture
def old_rejections(isolated_db):
    db.init_db()
    db.upsert_movie(_row("a", "IJE ELU - Latest Nigerian Nollywood Igbo Movie Full HD", BOILERPLATE, "igbo", "BLOCKED_TERM:clip"))
    db.upsert_movie(_row("b", "Igbo Movie Trailer", "An Igbo movie.", "igbo", "BLOCKED_TERM:trailer"))
    db.upsert_movie(_row("c", "Obi Mmiri Igbo Movie", "An Igbo movie.", "igbo", "USER_REJECTED"))
    db.upsert_movie(_row("d", "Eze Igbo Movie", "An Igbo movie.", "igbo", "WRONG_LANGUAGE"))
    db.upsert_movie(_row("y", "Aweja - Full Yoruba Movie 2025", BOILERPLATE, "yoruba", "BLOCKED_TERM:promo"))


def test_recheck_dry_run_reports_but_changes_nothing(old_rejections):
    result = recheck_rule_rejections("igbo")

    assert result["checked"] == 2                         # only rule rejections, not user/wrong-language ones
    assert [m["title"] for m in result["would_accept"]] == ["IJE ELU - Latest Nigerian Nollywood Igbo Movie Full HD"]
    assert result["still_rejected"] == 1
    assert db.count_movies("igbo", ACCEPTED_STATUSES) == 0


def test_recheck_apply_returns_only_the_false_positive_and_leaves_user_decisions(old_rejections):
    db.set_setting("target:igbo", 1000)

    result = recheck_rule_rejections("igbo", apply=True)

    assert result["accepted"] == 1
    statuses = {r["video_id"]: (r["status"], r["download_status"]) for r in db.list_movies("igbo", status="ALL")}
    assert statuses["a"][0] == "ACCEPTED" and statuses["a"][1] == "READY"
    assert statuses["b"][0] == "REJECTED"        # a real trailer title stays rejected
    assert statuses["c"][0] == "REJECTED"        # user decision untouched
    assert statuses["d"][0] == "REJECTED"        # wrong-language decision untouched


def test_recheck_respects_the_target(old_rejections):
    db.set_setting("target:igbo", 1)
    db.upsert_movie({**_row("z", "Existing Igbo Movie", "An Igbo movie.", "igbo", None), "status": "ACCEPTED"})

    result = recheck_rule_rejections("igbo", apply=True)

    assert result["accepted"] == 0 and result["blocked_by_target"] == 1
    assert db.count_movies("igbo", ACCEPTED_STATUSES) == 1


def test_yoruba_is_protected_from_being_rewritten(old_rejections):
    with pytest.raises(ValueError, match="protected"):
        recheck_rule_rejections("yoruba", apply=True)

    assert recheck_rule_rejections("yoruba")["would_accept"]          # a dry run is allowed
    assert [r["status"] for r in db.list_movies("yoruba", status="ALL")] == ["REJECTED"]
