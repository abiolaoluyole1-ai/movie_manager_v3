from movie_manager.utils import parse_iso8601_duration, format_duration, normalise_title, safe_filename


def test_duration_parser():
    assert parse_iso8601_duration("PT1H30M5S") == 5405
    assert parse_iso8601_duration("PT59M59S") == 3599
    assert parse_iso8601_duration("PT2H") == 7200


def test_duration_format():
    assert format_duration(5400) == "1h 30m"
    assert format_duration(3599) == "59m"


def test_normalise_title():
    assert normalise_title("ODUN BAKU - Full Yoruba Movie 2026") == "odun baku"


def test_safe_filename():
    assert ":" not in safe_filename("Movie: Part 1")
