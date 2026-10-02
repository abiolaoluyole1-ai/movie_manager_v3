import logging
import logging.handlers

from .config import DATA_DIR
from .events import redact_secrets

LOG_DIR = DATA_DIR / "logs"


class _RedactingFormatter(logging.Formatter):
    """Formats like the stock formatter, then strips API keys/tokens (tracebacks included)."""

    def format(self, record):
        return redact_secrets(super().format(record))


def setup_logging():
    """Terminal shows warnings/errors; data/logs/movie-manager.log keeps the full activity trail.

    Safe to call more than once.
    """
    root = logging.getLogger()
    if getattr(root, "_movie_manager_logging", False):
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = _RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    # Windows consoles are often cp1252; log lines contain symbols such as ✓/✕.
    if hasattr(console.stream, "reconfigure"):
        console.stream.reconfigure(errors="replace")
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "movie-manager.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(file_handler)
    root._movie_manager_logging = True
