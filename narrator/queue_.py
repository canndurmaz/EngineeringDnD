"""Priority queue of narration jobs. Lower priority number is served first."""
from __future__ import annotations

import heapq
import itertools
import threading

PRIORITY = {"turn": 0, "phase": 1, "genesis": 2}


class NarrationQueue:
    def __init__(self) -> None:
        self._heap: list = []
        self._counter = itertools.count()
        self._guard = threading.Condition()

    def submit(self, job: dict) -> None:
        priority = job.get("priority", PRIORITY.get(job.get("kind", "turn"), 1))
        with self._guard:
            heapq.heappush(self._heap, (priority, next(self._counter), job))
            self._guard.notify()

    def get(self, timeout: float = 1.0) -> "dict | None":
        with self._guard:
            if not self._heap:
                self._guard.wait(timeout)
            if not self._heap:
                return None
            return heapq.heappop(self._heap)[2]

    def pending(self) -> int:
        with self._guard:
            return len(self._heap)

    def drop_room(self, room_id: str) -> None:
        with self._guard:
            self._heap = [item for item in self._heap
                          if item[2].get("room_id") != room_id]
            heapq.heapify(self._heap)
