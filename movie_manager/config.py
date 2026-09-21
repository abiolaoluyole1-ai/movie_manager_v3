from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "movies.db"
DEFAULT_DOWNLOAD_ROOT = Path.home() / "Movies" / "Movie Manager"

CONCURRENCY_MIN = 1
CONCURRENCY_MAX = 7
CONCURRENCY_DEFAULT = 3
MIN_FREE_DISK_GB_DEFAULT = 20

DEFAULTS = {
    "active_language": "yoruba",
    "minimum_duration_minutes": "60",
    "maintain_target": "1",
    "download_root": str(DEFAULT_DOWNLOAD_ROOT),
    "target:yoruba": "30",
    "count_only_downloadable": "0",
    "max_concurrent_downloads": str(CONCURRENCY_DEFAULT),
    "movies_per_page": "30",
    "min_free_disk_gb": str(MIN_FREE_DISK_GB_DEFAULT),
    "download_quality": "1080",
    "youtube_use_browser_session": "0",
    "youtube_browser": "chrome",
}

DOWNLOAD_QUALITY_CHOICES = {"best", "1080", "720", "480"}
YOUTUBE_BROWSER_CHOICES = {"chrome", "edge", "firefox"}


def clamp_download_quality(value):
    return str(value) if str(value) in DOWNLOAD_QUALITY_CHOICES else "1080"


def clamp_youtube_browser(value):
    return str(value) if str(value) in YOUTUBE_BROWSER_CHOICES else "chrome"

NETWORK_RETRY_SECONDS = [5, 10, 20, 30, 60]

YOUTUBE_BLOCKED_MESSAGE = (
    "YouTube temporarily blocked this download. "
    "Try again later or reduce simultaneous downloads."
)
YOUTUBE_BLOCKED_COOLDOWN_SECONDS = 300


def is_youtube_blocked_error(exc):
    """True for a temporary YouTube protection response.

    yt-dlp also reports this short-lived restriction as "The page needs to
    be reloaded". That response must not be recorded as a permanent movie
    failure, because retrying a batch of it immediately prolongs the block.
    """
    message = str(exc).lower()
    return "not a bot" in message or "page needs to be reloaded" in message


def clamp_concurrency(value):
    """Clamps to the allowed 1-7 simultaneous-downloads range; any invalid
    (non-integer, missing) value safely falls back to the default of 3."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return CONCURRENCY_DEFAULT
    return max(CONCURRENCY_MIN, min(CONCURRENCY_MAX, value))


def clamp_min_free_disk_gb(value):
    """Clamps the low-disk-space guard threshold to a sane non-negative
    number; any invalid value safely falls back to the default of 20 GB."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return MIN_FREE_DISK_GB_DEFAULT
    if value < 0:
        return MIN_FREE_DISK_GB_DEFAULT
    return value
