PROFILES = {
    "yoruba": {
        "label": "Yoruba",
        "enabled": True,
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
        # Used by the Internet Archive provider (and any future provider that
        # searches by free-text phrase rather than YouTube's query params).
        "archive_queries": [
            "Yoruba movie",
            "Yoruba film",
            "Yoruba full movie",
            "old Yoruba movie",
            "classic Yoruba movie",
            "Nigerian Yoruba film",
            "Yoruba cinema",
            "Yoruba drama",
            "Yoruba traditional movie",
        ],
    },
    "igbo": {
        "label": "Igbo", "enabled": False, "region_code": "NG", "relevance_language": "ig",
        "queries": [], "archive_queries": [],
    },
    "hausa": {
        "label": "Hausa", "enabled": False, "region_code": "NG", "relevance_language": "ha",
        "queries": [], "archive_queries": [],
    },
}

BLOCKED_TERMS = {
    "trailer", "teaser", "clip", "clips", "short film", "short movie",
    "scene", "scenes", "interview", "behind the scenes", "bts",
    "review", "reaction", "soundtrack", "music video", "official music",
    "highlights", "making of", "preview", "promo", "episode", "episodes",
}

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
