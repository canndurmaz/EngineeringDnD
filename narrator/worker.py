"""One background thread. One generation at a time. Never blocks a turn."""
from __future__ import annotations

import concurrent.futures
import logging
import threading

from narrator.fallback import TemplateNarrator
from narrator.filters import clean

log = logging.getLogger(__name__)


def _allowed_numbers(job: dict) -> set:
    """Every number the engine actually committed, as strings."""
    allowed = set()
    hazard = job.get("hazard") or {}
    for value in (hazard.get("severity"), hazard.get("max_severity")):
        if value is not None:
            allowed.add(str(value))
    for change in job.get("changes", []):
        for key in ("amount", "value", "severity", "stamina", "focus", "count"):
            if key in change:
                allowed.add(str(change[key]))
    return allowed


class NarrationWorker:
    def __init__(self, queue, narrator, service, broker, fallback=None,
                 deadline: float = 45.0) -> None:
        self.queue = queue
        self.narrator = narrator
        self.service = service
        self.broker = broker
        self.fallback = fallback or TemplateNarrator()
        self.deadline = deadline
        self.thread: "threading.Thread | None" = None
        self._stop = threading.Event()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="narrator",
                                       daemon=True)
        self.thread.start()

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=join_timeout)
            self.thread = None
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once(timeout=0.5)

    # --- one job -----------------------------------------------------------

    def run_once(self, timeout: float = 1.0) -> bool:
        job = self.queue.get(timeout=timeout)
        if job is None:
            return False
        try:
            self._handle(job)
        except Exception:                       # a bad job must not kill the worker
            log.exception("narration job failed: %s", job.get("kind"))
        return True

    def _generate(self, job: dict) -> tuple:
        """Return (text, source). Falls back on error, timeout, or empty output."""
        try:
            future = self._pool.submit(self.narrator.narrate, job)
            raw = future.result(timeout=self.deadline)
            text = clean(raw, _allowed_numbers(job))
            if text:
                return text, self.narrator.name
            log.info("narration filtered to nothing; using template")
        except concurrent.futures.TimeoutError:
            log.warning("narration exceeded %.0fs deadline; using template",
                        self.deadline)
        except Exception:
            log.exception("narrator raised; using template")
        return self.fallback.narrate(job), self.fallback.name

    def _supports_streaming(self) -> bool:
        return callable(getattr(self.narrator, "stream", None))

    def _generate_streaming(self, job: dict) -> tuple:
        """Publish deltas as they arrive, then filter the assembled text."""
        room_id, event_seq = job["room_id"], job.get("event_seq")
        parts: list = []

        def consume():
            for delta in self.narrator.stream(job):
                parts.append(delta)
                self.broker.publish(room_id, {
                    "seq": event_seq, "kind": "narration_chunk",
                    "event_seq": event_seq, "delta": delta})
            return "".join(parts)

        try:
            raw = self._pool.submit(consume).result(timeout=self.deadline)
            text = clean(raw, _allowed_numbers(job))
            if text:
                return text, self.narrator.name
            log.info("streamed narration filtered to nothing; using template")
        except concurrent.futures.TimeoutError:
            log.warning("streamed narration exceeded %.0fs; using template",
                        self.deadline)
        except Exception:
            log.exception("stream raised; using template")
        return self.fallback.narrate(job), self.fallback.name

    def _handle(self, job: dict) -> None:
        kind = job.get("kind", "turn")
        if kind == "genesis":
            return self._handle_premise(job)
        if kind == "hazards":
            return self._handle_hazards(job)
        if kind == "phase":
            return self._handle_interlude(job)
        return self._handle_turn(job)

    def _handle_turn(self, job: dict) -> None:
        room_id, event_seq = job["room_id"], job.get("event_seq")
        room = self.service._room(room_id)
        if event_seq is not None:
            room.update_narration(event_seq, "streaming", "", "")
        if self._supports_streaming():
            text, source = self._generate_streaming(job)
        else:
            text, source = self._generate(job)
        if event_seq is not None:
            room.update_narration(event_seq, "done", text, source)
        payload = {"event_seq": event_seq, "text": text, "source": source,
                   "job_kind": job.get("kind", "turn")}
        seq = room.append_event("narration", None, payload)
        self.broker.publish(room_id, {"seq": seq, "kind": "narration", **payload})

    def _handle_premise(self, job: dict) -> None:
        text, source = self._generate(job)
        self.service.set_premise(job["room_id"], text)
        room = self.service._room(job["room_id"])
        payload = {"premise": text, "source": source}
        seq = room.append_event("premise", None, payload)
        self.broker.publish(job["room_id"],
                            {"seq": seq, "kind": "premise", **payload})

    def _handle_interlude(self, job: dict) -> None:
        """The phase-gate interlude. It belongs to no single action, so it has
        no event_seq and never touches the narrations table -- it is published
        as its own `interlude` event."""
        text, source = self._generate(job)
        room = self.service._room(job["room_id"])
        payload = {"phase": job.get("phase", ""), "text": text, "source": source}
        seq = room.append_event("interlude", None, payload)
        self.broker.publish(job["room_id"],
                            {"seq": seq, "kind": "interlude", **payload})

    def _handle_hazards(self, job: dict) -> None:
        from narrator.genesis import parse_hazard_lines
        try:
            raw = self._pool.submit(self.narrator.narrate, job).result(
                timeout=self.deadline)
            entries = parse_hazard_lines(raw, job["count"])
        except Exception:
            log.info("hazard generation failed; keeping template hazards")
            return                      # the deterministic hazards stay in place
        if not entries:
            return
        self.service.rename_hazards(job["room_id"], job["phase_index"], entries)
        room = self.service._room(job["room_id"])
        payload = {"phase_index": job["phase_index"],
                   "names": [n for n, _ in entries]}
        seq = room.append_event("campaign_updated", None, payload)
        self.broker.publish(job["room_id"],
                            {"seq": seq, "kind": "campaign_updated", **payload})
