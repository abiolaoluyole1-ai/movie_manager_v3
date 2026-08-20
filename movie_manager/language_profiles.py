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
    },
    "igbo": {"label": "Igbo", "enabled": False, "region_code": "NG", "relevance_language": "ig", "queries": []},
    "hausa": {"label": "Hausa", "enabled": False, "region_code": "NG", "relevance_language": "ha", "queries": []},
}

BLOCKED_TERMS = {
    "trailer", "teaser", "clip", "clips", "short film", "short movie",
    "scene", "scenes", "interview", "behind the scenes", "bts",
    "review", "reaction", "soundtrack", "music video", "official music",
    "highlights", "making of", "preview", "promo", "episode", "episodes",
}
