from movie_manager.language_profiles import PROFILES, BLOCKED_TERMS


def test_all_three_languages_are_enabled_with_their_own_profile():
    for key, label in (("yoruba", "Yoruba"), ("igbo", "Igbo"), ("hausa", "Hausa")):
        profile = PROFILES[key]
        assert profile["enabled"] is True
        assert profile["label"] == label
        assert profile["folder"] == label
        assert profile["queries"]
        assert profile["title_keywords"] and profile["language_tags"]


def test_blocklist():
    assert "trailer" in BLOCKED_TERMS
    assert "interview" in BLOCKED_TERMS
