"""Process-wide fan-out of room events to connected SSE clients."""
from __future__ import annotations

import queue
import threading

_MAX_PENDING = 200


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict = {}
        self._guard = threading.Lock()

    def subscribe(self, room_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=_MAX_PENDING)
        with self._guard:
            self._subscribers.setdefault(room_id, []).append(q)
        return q

    def unsubscribe(self, room_id: str, q: queue.Queue) -> None:
        with self._guard:
            listeners = self._subscribers.get(room_id, [])
            if q in listeners:
                listeners.remove(q)
            if not listeners:
                self._subscribers.pop(room_id, None)

    def subscriber_count(self, room_id: str) -> int:
        with self._guard:
            return len(self._subscribers.get(room_id, []))

    def publish(self, room_id: str, payload: dict) -> None:
        with self._guard:
            listeners = list(self._subscribers.get(room_id, []))
        for q in listeners:
            try:
                q.put_nowait(payload)
            except queue.Full:
                # A client that cannot keep up is dropped; it will reconnect with
                # Last-Event-ID and replay whatever it missed.
                self.unsubscribe(room_id, q)
