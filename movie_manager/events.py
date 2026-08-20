import json
import queue
import threading
from datetime import datetime, timezone


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
