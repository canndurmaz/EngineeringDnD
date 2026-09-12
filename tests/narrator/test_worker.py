import pytest
from broker import EventBroker
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from narrator.base import FakeNarrator
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker
from service import GameService


class BoomNarrator:
    name = "boom"

    def narrate(self, job):
        raise RuntimeError("model exploded")


class SlowNarrator:
    name = "slow"

    def narrate(self, job):
        import time
        time.sleep(0.3)
        return "Eventually, prose."


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


def make_job(room_id, seq):
    return {"kind": "turn", "priority": 0, "room_id": room_id, "event_seq": seq,
            "actor_name": "Ada", "actor_class": "Computer Scientist",
            "ability_name": "Unit Test Barrage", "outcome": "success",
            "natural": 12, "total": 15, "dc": 13, "phase": "Requirements",
            "premise": "A trainer aircraft", "hazard": None, "changes": []}


def test_run_once_on_an_empty_queue_returns_false(rig):
    svc, _, _ = rig
    worker = NarrationWorker(NarrationQueue(), FakeNarrator(), svc, EventBroker())
    assert worker.run_once(timeout=0.01) is False


def test_worker_writes_the_narration_to_the_room(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator("The suite goes green."), svc,
                    EventBroker()).run_once(timeout=1)
    stored = svc.narration(room_id, seq)
    assert stored["status"] == "done"
    assert stored["text"] == "The suite goes green."
    assert stored["source"] == "fake"


def test_worker_appends_a_narration_event_so_replay_works(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator(), svc, EventBroker()).run_once(timeout=1)
    kinds = [e["kind"] for e in svc.events_since(room_id, 0)]
    assert "narration" in kinds


def test_narration_event_references_its_action(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator(), svc, EventBroker()).run_once(timeout=1)
    event = [e for e in svc.events_since(room_id, 0) if e["kind"] == "narration"][0]
    assert event["payload"]["event_seq"] == seq


def test_worker_publishes_to_the_broker(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator(), svc, broker).run_once(timeout=1)
    # Assert a narration was published, not that it was FIRST: once Task 21 adds
    # token streaming, narration_chunk events precede it on the same broker.
    kinds = []
    while True:
        try:
            kinds.append(sub.get(timeout=0.3)["kind"])
        except Exception:
            break
    assert "narration" in kinds


def test_model_failure_falls_back_to_the_template(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, BoomNarrator(), svc, EventBroker()).run_once(timeout=1)
    stored = svc.narration(room_id, seq)
    assert stored["status"] == "done"
    assert stored["source"] == "template"
    assert stored["text"].strip()


def test_exceeding_the_deadline_falls_back_to_the_template(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, SlowNarrator(), svc, EventBroker(),
                    deadline=0.05).run_once(timeout=1)
    assert svc.narration(room_id, seq)["source"] == "template"


def test_filter_rejecting_everything_falls_back_to_the_template(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator("You rolled a natural 20. DC 13 beaten."),
                    svc, EventBroker()).run_once(timeout=1)
    assert svc.narration(room_id, seq)["source"] == "template"


def test_worker_thread_starts_and_stops_cleanly(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    worker = NarrationWorker(q, FakeNarrator(), svc, EventBroker())
    worker.start()
    q.submit(make_job(room_id, seq))
    import time
    for _ in range(50):
        if svc.narration(room_id, seq)["status"] == "done":
            break
        time.sleep(0.02)
    worker.stop()
    assert svc.narration(room_id, seq)["status"] == "done"
    assert worker.thread is None or not worker.thread.is_alive()
