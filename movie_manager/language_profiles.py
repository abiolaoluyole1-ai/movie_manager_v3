from datetime import datetime, timezone

DEFAULT_TARGET = 1000

# Each profile fully describes one language: where its files go, how YouTube is
# searched and what counts as evidence that a video is really in that language.
#
#   folder              download sub-folder under the download root
#   queries             base YouTube searches (their saved page state is kept as-is)
#   extra_queries       more phrasings/genres, searched after the base queries
#   window_queries      queries re-run once per publish-date window (see build_search_plans)
#   year_from           oldest publish year used for the date windows
#   language_tags       YouTube defaultAudioLanguage/defaultLanguage values meaning this language
#   title_keywords      title evidence
#   description_phrases description evidence (specific phrases, not single words)
#   channel_keywords    channel-name evidence
#   query_markers       a search is "language specific" when its text contains one of these
#   cast_keywords       well-known performers/brands that only make this language's films
#   whole_word_keywords match keywords as whole words ("igbo" must not fire inside
#                       the actor names Igboegwu / Igboanugo)
#   title_noise         look-alike phrases removed from titles before matching keywords
#   reject_other_language  reject candidates that carry another language's evidence
#                       (not for Yoruba: "igbo" is the Yoruba word for forest)
PROFILES = {
    "yoruba": {
        "label": "Yoruba",
        "enabled": True,
        # The Yoruba goal is complete: its catalogue is never rewritten by re-check tools.
        "protected": True,
        "folder": "Yoruba",
        "region_code": "NG",
        # YouTube search.list rejects relevanceLanguage=yo with HTTP 400.
        # The Yoruba terms in each query remain the language constraint.
        "relevance_language": None,
        "queries": [
            "old Yoruba full movie",
            "classic Yoruba full movie",
            "Yoruba traditional full movie",
            "Yoruba full movie",
            "Yoruba Nollywood full movie",
            "old Yoruba film",
            "classic Yoruba film",
            "Yoruba epic full movie",
            "Yoruba drama full movie",
            "Yoruba family full movie",
            "Yoruba comedy full movie",
        ],
        "extra_queries": [
            "Yoruba movie",
            "latest Yoruba movie",
            "Nigerian Yoruba movie",
            "Yoruba language movie",
            "Yoruba film full length",
            "Yoruba love story full movie",
            "Yoruba action full movie",
            "Yoruba thriller full movie",
            "Yoruba horror full movie",
            "Yoruba romance full movie",
            "Yoruba village full movie",
            "Yoruba Islamic full movie",
            "Yoruba gospel full movie",
            "Yoruba blockbuster movie",
            "Yoruba epic movie",
            "Yoruba Nollywood movie",
        ],
        "window_queries": ["Yoruba full movie", "Yoruba movie"],
        "year_from": 2008,
        # Used by the Internet Archive provider (and any future provider that
        # searches by free-text phrase rather than YouTube's query params).
        "archive_queries": [
            "Yoruba movie",
            "Yoruba film",
            "Yoruba full movie",
            "Nigerian Yoruba movie",
            "Nigerian Yoruba film",
            "old Yoruba movie",
            "classic Yoruba movie",
            "Yoruba cinema",
            "Yoruba drama",
            "Yoruba feature film",
            "Yoruba Nollywood",
            "Yoruba language film",
            "Yoruba traditional movie",
        ],
        "language_tags": ["yo", "yor", "yoruba"],
        "title_keywords": ["yoruba", "yorùbá"],
        "description_phrases": ["yoruba movie", "yoruba film", "yoruba language", "yoruba nollywood"],
        "channel_keywords": ["yoruba", "yorùbá"],
        "query_markers": ["yoruba"],
        "title_noise": [],
        "reject_other_language": False,
    },
    "igbo": {
        "label": "Igbo",
        "enabled": True,
        "folder": "Igbo",
        "region_code": "NG",
        "relevance_language": None,
        "queries": [
            "Igbo movie",
            "Igbo film",
            "Igbo full movie",
            "latest Igbo movie",
            "old Igbo movie",
            "classic Igbo movie",
            "Nigerian Igbo movie",
            "Igbo Nollywood movie",
            "Igbo language film",
        ],
        "extra_queries": [
            "new Igbo movie",
            "Igbo language movie",
            "Igbo traditional movie",
            "Igbo village movie",
            "Igbo epic movie",
            "Igbo drama",
            "Igbo comedy movie",
            "Igbo movie full length",
            "Igbo traditional full movie",
            "Igbo epic full movie",
            "Igbo drama full movie",
            "Igbo comedy full movie",
            "Igbo family full movie",
            "Igbo love story full movie",
            "Igbo village full movie",
            "Igbo action full movie",
            "Igbo native full movie",
            "Igbo native language movie",
            "Nigerian Igbo full movie",
            "Nollywood Igbo full movie",
            "Nollywood Igbo language movie",
            "Igbo blockbuster movie",
            "Ndi Igbo full movie",
            "Igbo culture movie",
            "Igbo masquerade movie",
            "Igbo king kingdom movie",
            "Igbo native doctor movie",
            "Igbo wedding movie",
        ],
        "window_queries": ["Igbo full movie", "Igbo movie", "latest Igbo movie"],
        "year_from": 2012,
        "archive_queries": [],
        "language_tags": ["ig", "ibo", "igbo"],
        "whole_word_keywords": True,
        "title_keywords": [
            "igbo", "ìgbò", "ndi igbo", "ibo movie", "ibo film", "chief imo", "chiefimo", "chiefimocomedy",
        ],
        "description_phrases": [
            "igbo movie", "igbo film", "igbo language", "igbo nollywood", "igbo version", "igbo full movie",
            "igbo epic", "igbo comedy", "igbo drama", "igbo traditional", "igbo native", "igbo culture",
            "chief imo comedy",
        ],
        "channel_keywords": ["igbo", "ìgbò", "chief imo"],
        "query_markers": ["igbo"],
        # "Igbo" is also the Yoruba word for forest/bush.
        "title_noise": ["igbo irunmole", "igbo olodumare", "igbo ora", "igbo-ora", "igbo ikoko"],
        "reject_other_language": True,
    },
    "hausa": {
        "label": "Hausa",
        "enabled": True,
        "folder": "Hausa",
        "region_code": "NG",
        "relevance_language": None,
        "queries": [
            "Hausa movie",
            "Hausa film",
            "Hausa full movie",
            "latest Hausa movie",
            "old Hausa movie",
            "classic Hausa movie",
            "Nigerian Hausa movie",
            "Kannywood movie",
            "Hausa language film",
        ],
        "extra_queries": [
            "new Hausa movie",
            "Hausa language movie",
            "Kannywood full movie",
            "latest Kannywood movie",
            "new Kannywood movie",
            "Hausa drama",
            "Hausa comedy movie",
            "Hausa traditional movie",
            "Hausa full film",
            "Hausa film full length",
            "Hausa movie with English subtitles",
            "complete Hausa film",
            "original Hausa film",
            "sabon fim Hausa",
            "fim din Hausa",
            "Hausa drama full movie",
            "Hausa love story full movie",
            "Hausa action full movie",
            "Hausa comedy full movie",
            "Hausa family full movie",
            "Hausa epic movie",
            "Hausa Arewa movie",
            "Arewa film full movie",
            "Hausa Nollywood movie",
            "Kannywood film with English subtitles",
        ],
        "window_queries": ["Hausa full movie", "Kannywood movie", "Hausa film"],
        "year_from": 2012,
        "archive_queries": [],
        "language_tags": ["ha", "hau", "hausa"],
        "whole_word_keywords": True,
        "title_keywords": ["hausa", "kannywood", "sabon fim", "fim din", "hausa dub"],
        "description_phrases": [
            "hausa movie", "hausa film", "hausa language", "kannywood", "hausa nollywood", "fim din hausa",
            "hausa full", "hausa drama", "hausa comedy", "hausa series",
        ],
        "channel_keywords": ["hausa", "kannywood", "arewa"],
        # Kannywood stars: a film that stars them is a Hausa-language film even when the
        # title never says "Hausa" (titles are often just a Hausa name plus "Full Movie").
        "cast_keywords": [
            "ali nuhu", "adam a zango", "adam zango", "sadiq sani sadiq", "maryam yahaya", "jamila nagudu",
            "hadiza gabon", "rabi'u rikadawa", "rabiu rikadawa", "momme gombe", "nafisat abdullahi",
            "aisha aliyu tsamiya", "saratu gidado", "umar m shariff", "yakubu muhammad", "baba ari",
            "garzali miko", "mansura isah", "hafsat idris", "ado gwanja", "ali artwork", "sani danja",
            "ibrahim maishinku", "abdul m shareef", "lilin baba", "falalu a dorayi", "mustapha naburaska",
            "nuhu abdullahi", "fati washa", "samha m inuwa", "teema yola", "aminu saira",
        ],
        "query_markers": ["hausa", "kannywood", "arewa", "fim"],
        "title_noise": [],
        "reject_other_language": True,
    },
}


def language_folder(language):
    """Download sub-folder name for a language (e.g. 'Yoruba')."""
    profile = PROFILES.get(language) or {}
    return profile.get("folder") or str(language).capitalize()


def public_languages():
    """The small, JSON-friendly view of the profiles the browser needs."""
    return {key: {"label": p["label"], "enabled": bool(p["enabled"])} for key, p in PROFILES.items()}


def _publish_windows(profile, today):
    """(label, publishedAfter date, publishedBefore date), newest first.

    YouTube search returns at most ~500 results per query, so one query can
    never reach the whole catalogue. Re-running it once per publish-date
    window gives each window its own results. Recent years hold most of the
    uploads, so they are split into half-years.
    """
    this_year = today.year
    for year in range(this_year, profile["year_from"] - 1, -1):
        if year >= this_year - 5:
            yield f"{year} H2", f"{year}-07-01", f"{year + 1}-01-01"
            yield f"{year} H1", f"{year}-01-01", f"{year}-07-01"
        else:
            yield str(year), f"{year}-01-01", f"{year + 1}-01-01"


def build_search_plans(language, today=None):
    """Ordered YouTube search strategies for a language.

    Each plan is {key, query, label, params}. `key` is what the page-token /
    exhausted state is saved under, so it must stay stable: base queries keep
    their plain query text as the key (their existing saved progress still
    applies); every other plan gets a "|suffix" so it has its own progress.
    """
    profile = PROFILES[language]
    today = today or datetime.now(timezone.utc)
    plans, seen = [], set()

    def add(query, suffix="", label=None, params=None):
        key = f"{query}|{suffix}" if suffix else query
        if key in seen:
            return
        seen.add(key)
        plans.append({"key": key, "query": query, "label": label or query, "params": params or {}})

    for query in profile["queries"]:
        add(query)
    for query in profile.get("extra_queries", []):
        add(query)
    for label, after, before in _publish_windows(profile, today):
        for query in profile.get("window_queries", []):
            add(query, label, f"{query} ({label})", {
                "publishedAfter": f"{after}T00:00:00Z", "publishedBefore": f"{before}T00:00:00Z",
            })
    for order, order_label in (("date", "newest first"), ("viewCount", "most viewed")):
        for query in profile["queries"]:
            add(query, f"order={order}", f"{query} ({order_label})", {"order": order})
    return plans


BLOCKED_TERMS = {
    "trailer", "teaser", "clip", "clips", "short film", "short movie",
    "scene", "scenes", "interview", "behind the scenes", "bts",
    "review", "reaction", "soundtrack", "music video", "official music",
    "highlights", "making of", "preview", "promo", "episode", "episodes",
}

# Long uploads that are not movies at all (kids' songs, music mixes, lessons, sermons).
# Matched as whole words in the TITLE only.
NON_MOVIE_FORMAT_TERMS = {
    "nursery rhymes", "nursery rhyme", "cartoon", "cartoons", "lullaby", "lullabies",
    "children's songs", "kids songs", "gospel songs", "worship songs", "praise songs",
    "playlist", "mixtape", "tutorial", "alphabet", "sermon",
}

# A description only counts against a video when it says what THIS video is.
# Single words ("clip", "promo", "scenes") and tag-style lists ("yoruba movie clips,
# movie trailer, behind the scenes, movie review") are channel boilerplate that sits
# under real movies, so they are never enough on their own.
DESCRIPTION_NON_MOVIE_PHRASES = (
    "official trailer", "this trailer", "this teaser", "this clip", "this is a clip",
    "in this interview", "full interview", "interview with", "reaction video", "reacts to",
    "official music video", "lyric video", "official audio",
)

# Boilerplate/tag phrases: only meaningful as support when the channel itself posts extracts.
DESCRIPTION_TAG_PHRASES = (
    "movie trailer", "teaser trailer", "movie clip", "short clip", "clip from", "clips from",
    "behind the scenes", "behind-the-scenes", "highlights of", "movie highlights", "movie review",
    "music video", "movie promo", "promo video", "official promo", "making of",
)

# Channels that exist to post short extracts rather than whole movies.
NON_MOVIE_CHANNEL_WORDS = ("clip", "trailer", "highlight", "teaser", "reaction", "review", "promo")

# Titles are reliable indicators for these explicit non-movie formats. Description
# matching intentionally excludes generic words such as "collection".
COMPILATION_TITLE_PHRASES = {
    "best of", "compilation", "movie compilation", "comedy compilation",
    "collection of scenes", "funny moments", "funniest moments", "highlights",
    "scene compilation", "scenes compilation",
}

COMPILATION_DESCRIPTION_PHRASES = {
    "movie compilation", "comedy compilation", "collection of scenes",
    "funny moments", "funniest moments", "scene compilation", "scenes compilation",
}
