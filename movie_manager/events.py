import json
import logging
import queue
import re
import threading
from collections import deque
from datetime import datetime, timezone

_SECRET_RE = re.compile(r"(?i)\b(key|api_key|apikey|token|access_token|cookie)=[^&\s'\"]+")
activity_logger = logging.getLogger("movie_manager.activity")


def redact_secrets(text):
    """Strip API keys/tokens from text before it reaches the Live Log or log files.

    requests puts the full request URL (including ?key=...) into connection
    error messages, so any exception text must pass through here.
    """
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", str(text))


class EventBus:
    def __init__(self):
        self._clients = set()
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._clients.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._clients.discard(q)

    def emit(self, event_type, payload=None):
        event = {
            "type": event_type,
            "payload": payload or {},
            "at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass

    @staticmethod
    def encode(event):
        return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


events = EventBus()


class ActivityLog:
    """Chronological human-readable log shown on the Dashboard.

    Discovery and download workers write one line per meaningful step. Lines
    are kept in a bounded in-memory buffer (so a page reload or a late-opened
    browser tab can show recent history) and pushed live over SSE as `log`
    events. Levels: info, good, warn, bad, dim.
    """

    def __init__(self, bus, maxlen=500):
        self._bus = bus
        self._entries = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._next_id = 1

    def add(self, message, level="info", scope="app", language=None):
        message = redact_secrets(message)
        with self._lock:
            entry = {
                "id": self._next_id,
                "at": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "scope": scope,
                "language": language,
                "message": message,
            }
            self._next_id += 1
            self._entries.append(entry)
        activity_logger.log(
            logging.WARNING if level == "bad" else logging.INFO,
            "[%s%s] %s", scope, f"/{language}" if language else "", message,
        )
        self._bus.emit("log", entry)
        return entry

    def recent(self, limit=200, after=0, language=None):
        with self._lock:
            entries = [
                e for e in self._entries
                if e["id"] > after and (language is None or e["language"] in (None, language))
            ]
        return entries[-limit:] if limit else entries

    def clear(self):
        with self._lock:
            self._entries.clear()


activity = ActivityLog(events)
