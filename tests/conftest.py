import pytest

from movie_manager import db


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """A fresh SQLite DB per test, in pytest's own managed temp directory --
    never the project root -- so test runs never leave clutter behind."""
    path = tmp_path / "movie-manager-test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    yield path
