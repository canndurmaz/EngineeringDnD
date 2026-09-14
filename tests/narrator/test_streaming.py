import pytest
from broker import EventBroker
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker
from service import GameService
from tests.narrator.test_worker import make_job


class StreamingNarrator:
    name = "stream"

    def __init__(self, chunks):
        self.chunks = chunks

    def narrate(self, job):
        return "".join(self.chunks)

    def stream(self, job):
        yield from self.chunks


class NoStream:
    """A narrator with no stream(), proving the non-streaming path still works."""

    name = "nostream"

    def narrate(self, job):
        return "It holds."


class BrokenStreamNarrator:
    name = "broken"

    def narrate(self, job):
        return "Fallback prose from narrate."

    def stream(self, job):
        yield "The bracket "
        raise RuntimeError("stream died mid-token")


@pytest.fixture
def rig(tmp_path):
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes())
    room_id = svc.create_room("Kestrel", "aircraft")
    a = svc.join_room(room_id, "Ada", "computer_scientist")
    svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    result = svc.act(room_id, a["player_id"], "unit_test_barrage")
    return svc, room_id, result["event_seq"]


def drain(sub):
    out = []
    while True:
        try:
            out.append(sub.get(timeout=0.2))
        except Exception:
            return out


def test_chunks_are_published_as_they_arrive(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["The ", "bracket ", "holds."]),
                    svc, broker).run_once(timeout=1)
    kinds = [e["kind"] for e in drain(sub)]
    assert kinds.count("narration_chunk") == 3
    assert kinds[-1] == "narration"


def test_chunk_payloads_reassemble_into_the_final_text(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["The ", "bracket ", "holds."]),
                    svc, broker).run_once(timeout=1)
    events = drain(sub)
    assembled = "".join(e["delta"] for e in events if e["kind"] == "narration_chunk")
    assert assembled == "The bracket holds."


def test_chunks_reference_their_action_event(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["Done."]), svc, broker).run_once(timeout=1)
    chunk = [e for e in drain(sub) if e["kind"] == "narration_chunk"][0]
    assert chunk["event_seq"] == seq


def test_chunks_are_not_written_to_the_durable_log(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["The ", "bracket ", "holds."]),
                    svc, EventBroker()).run_once(timeout=1)
    kinds = [e["kind"] for e in svc.events_since(room_id, 0)]
    assert "narration_chunk" not in kinds
    assert kinds.count("narration") == 1


def test_streamed_text_is_still_filtered(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["You rolled ", "a natural 20."]),
                    svc, EventBroker()).run_once(timeout=1)
    stored = svc.narration(room_id, seq)
    assert stored["source"] == "template"      # filter rejected everything
    assert "natural 20" not in stored["text"]


def test_a_stream_that_dies_falls_back_to_the_template(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, BrokenStreamNarrator(), svc,
                    EventBroker()).run_once(timeout=1)
    stored = svc.narration(room_id, seq)
    assert stored["status"] == "done" and stored["text"].strip()
    assert stored["source"] == "template"


def test_a_narrator_without_stream_still_works(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, NoStream(), svc, broker).run_once(timeout=1)
    assert [e["kind"] for e in drain(sub)] == ["narration"]
