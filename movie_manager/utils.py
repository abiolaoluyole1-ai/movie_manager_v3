import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_iso8601_duration(value: str) -> int:
    if not value:
        return 0
    match = _DURATION_RE.match(value)
    if not match:
        return 0
    parts = {k: int(v or 0) for k, v in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def normalise_title(title: str) -> str:
    text = (title or "").lower()
    text = re.sub(r"\b(19|20)\d{2}\b", " ", text)
    text = re.sub(r"\b(full|complete|latest|new|official|yoruba|nollywood|movie|film|part\s*\d+)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def safe_filename(name: str, max_len: int = 150) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "movie")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:max_len].strip() or "movie")


def extension_from_url(url: str, default: str = ".mp4") -> str:
    try:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}:
            return suffix
    except Exception:
        pass
    return default


def is_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


YOUTUBE_HOST_FRAGMENTS = ("youtube.com", "youtu.be")


def free_disk_space_gb(path) -> float:
    """Free space in GiB on the drive containing `path`. `path` need not
    exist yet -- checks the nearest existing ancestor directory."""
    p = Path(str(path)).expanduser()
    while not p.exists():
        parent = p.parent
        if parent == p:
            break
        p = parent
    usage = shutil.disk_usage(str(p))
    return usage.free / (1024 ** 3)


def is_direct_http_candidate(url: str) -> bool:
    """True only for a genuine, non-YouTube, http(s) direct-file URL.

    A youtube.com/watch or youtu.be URL is metadata/playback only and must
    never be treated as a direct download source.
    """
    if not is_http_url(url):
        return False
    lower = url.lower()
    return not any(fragment in lower for fragment in YOUTUBE_HOST_FRAGMENTS)


def format_bytes(value) -> str:
    """Human-readable size/speed, e.g. 4.8 MB."""
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
