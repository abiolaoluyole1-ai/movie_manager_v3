from movie_manager.language_profiles import PROFILES, BLOCKED_TERMS


def test_yoruba_first():
    assert PROFILES["yoruba"]["enabled"] is True
    assert PROFILES["igbo"]["enabled"] is False
    assert PROFILES["hausa"]["enabled"] is False


def test_blocklist():
    assert "trailer" in BLOCKED_TERMS
    assert "interview" in BLOCKED_TERMS
