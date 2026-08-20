from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "movies.db"
DEFAULT_DOWNLOAD_ROOT = Path.home() / "Movies" / "Movie Manager"

DEFAULTS = {
    "active_language": "yoruba",
    "minimum_duration_minutes": "60",
    "maintain_target": "1",
    "download_root": str(DEFAULT_DOWNLOAD_ROOT),
    "target:yoruba": "30",
}

NETWORK_RETRY_SECONDS = [5, 10, 20, 30, 60]
