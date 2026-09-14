"""The table waits for the DM.

The gate is server state on purpose: two browsers must never disagree about
whether the game is paused. So every test here drives the service (or the HTTP
API) rather than the page, and asks the only question that matters -- can a move
land right now?
"""
import pytest
from broker import EventBroker
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from narrator.base import FakeNarrator
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker
from service import DEFAULT_DM_WAIT, GameService, ServiceError, dm_wait_seconds


@pytest.fixture
def rig(tmp_path):
    """A started two-seat room with a real narration queue, so acting gates."""
    queue = NarrationQueue()
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes(), queue=queue, broker=EventBroker())
    room_id = svc.create_room("Kestrel", "aircraft")
    ada = svc.join_room(room_id, "Ada", "computer_scientist")
    ben = svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    return svc, queue, room_id, ada["player_id"], ben["player_id"]


def drain(queue, narrator, svc, kind="turn"):
    worker = NarrationWorker(queue, narrator, svc, EventBroker())
    while worker.run_once(timeout=0.05):
        pass
    return worker


# --- the gate itself --------------------------------------------------------

def test_a_room_with_no_narrator_never_gates(tmp_path):
    """No queue means nothing will ever arrive to open the gate again, so the
    table must not be made to wait for it."""
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes())
    room_id = svc.create_room("Kestrel", "aircraft")
    ada = svc.join_room(room_id, "Ada", "computer_scientist")
    svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    svc.act(room_id, ada["player_id"], "unit_test_barrage")
    assert svc.dm_gate(room_id)["waiting"] is False


def test_acting_opens_the_gate_on_the_action_that_queued_the_narration(rig):
    svc, _, room_id, ada, _ = rig
    result = svc.act(room_id, ada, "unit_test_barrage")
    gate = svc.dm_gate(room_id)
    assert gate["waiting"] is True
    assert gate["event_seq"] == result["event_seq"]


def test_act_is_refused_while_the_room_is_gated(rig):
    svc, _, room_id, ada, ben = rig
    svc.act(room_id, ada, "unit_test_barrage")
    with pytest.raises(ServiceError, match="the DM is still writing"):
        svc.act(room_id, ben, "shop_floor_fix")


def test_end_turn_is_refused_while_the_room_is_gated(rig):
    svc, _, room_id, ada, ben = rig
    svc.act(room_id, ada, "unit_test_barrage")
    with pytest.raises(ServiceError, match="the DM is still writing"):
        svc.end_turn(room_id, ben)


def test_the_next_move_lands_once_the_narration_event_arrives(rig):
    svc, queue, room_id, ada, ben = rig
    svc.act(room_id, ada, "unit_test_barrage")
    drain(queue, FakeNarrator("The suite goes green."), svc)
    kinds = [e["kind"] for e in svc.events_since(room_id, 0)]
    assert "narration" in kinds
    assert svc.dm_gate(room_id)["waiting"] is False
    assert svc.act(room_id, ben, "shop_floor_fix")["event_seq"]


# --- the three fallback paths -----------------------------------------------

class BoomNarrator:
    name = "boom"

    def narrate(self, job):
        raise RuntimeError("model exploded")


class SlowNarrator:
    name = "slow"

    def narrate(self, job):
        import time
        time.sleep(1.0)
        return "Eventually, prose."


class EmptyNarrator:
    """Answers only with text the filter strips to nothing."""
    name = "empty"

    def narrate(self, job):
        return "   "


@pytest.mark.parametrize("narrator,deadline", [
    (BoomNarrator(), 5.0),          # the model raised
    (SlowNarrator(), 0.05),         # the model blew the deadline
    (EmptyNarrator(), 5.0),         # the filter emptied the output
])
def test_the_gate_clears_on_every_fallback_path(rig, narrator, deadline):
    """A gate that outlives a failed narration is a hung table."""
    svc, queue, room_id, ada, ben = rig
    svc.act(room_id, ada, "unit_test_barrage")
    assert svc.dm_gate(room_id)["waiting"] is True
    worker = NarrationWorker(queue, narrator, svc, EventBroker(),
                             deadline=deadline)
    while worker.run_once(timeout=0.05):
        pass
    assert svc.dm_gate(room_id)["waiting"] is False
    assert svc.act(room_id, ben, "shop_floor_fix")["event_seq"]


def test_a_fallback_still_writes_a_narration_event(rig):
    svc, queue, room_id, ada, _ = rig
    svc.act(room_id, ada, "unit_test_barrage")
    drain(queue, BoomNarrator(), svc)
    kinds = [e["kind"] for e in svc.events_since(room_id, 0)]
    assert "narration" in kinds


# --- the deadline ------------------------------------------------------------

def test_the_gate_clears_after_the_deadline_even_with_no_narration(rig,
                                                                   monkeypatch):
    """A wedged model must never freeze a table permanently."""
    monkeypatch.setenv("CP_DM_WAIT", "0.05")
    svc, _, room_id, ada, ben = rig
    svc.act(room_id, ada, "unit_test_barrage")
    assert svc.dm_gate(room_id)["waiting"] is True
    import time
    time.sleep(0.08)
    assert svc.dm_gate(room_id)["waiting"] is False
    assert svc.act(room_id, ben, "shop_floor_fix")["event_seq"]


def test_the_wait_defaults_to_twenty_seconds(monkeypatch):
    monkeypatch.delenv("CP_DM_WAIT", raising=False)
    assert dm_wait_seconds() == DEFAULT_DM_WAIT == 20.0


def test_a_malformed_wait_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("CP_DM_WAIT", "soon")
    assert dm_wait_seconds() == DEFAULT_DM_WAIT


def test_the_wait_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("CP_DM_WAIT", "3.5")
    assert dm_wait_seconds() == 3.5


# --- skipping ----------------------------------------------------------------

def test_skipping_clears_the_gate_and_lets_the_next_move_land(rig):
    svc, _, room_id, ada, ben = rig
    svc.act(room_id, ada, "unit_test_barrage")
    svc.skip_dm(room_id)
    assert svc.dm_gate(room_id)["waiting"] is False
    assert svc.act(room_id, ben, "shop_floor_fix")["event_seq"]


def test_skipping_appends_an_event_so_every_client_learns_at_once(rig):
    svc, _, room_id, ada, _ = rig
    result = svc.act(room_id, ada, "unit_test_barrage")
    svc.skip_dm(room_id)
    skipped = [e for e in svc.events_since(room_id, 0) if e["kind"] == "dm_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["payload"]["event_seq"] == result["event_seq"]


def test_skipping_does_not_cancel_the_narration(rig):
    """The prose still lands afterwards and renders in its own place."""
    svc, queue, room_id, ada, _ = rig
    result = svc.act(room_id, ada, "unit_test_barrage")
    svc.skip_dm(room_id)
    drain(queue, FakeNarrator("The suite goes green."), svc)
    stored = svc.narration(room_id, result["event_seq"])
    assert stored["status"] == "done" and stored["text"]


def test_a_late_narration_does_not_open_a_gate_it_does_not_own(rig):
    """After a skip the next turn opens a *new* gate; the skipped turn's
    narration arriving late must not open it."""
    svc, queue, room_id, ada, ben = rig
    first = svc.act(room_id, ada, "unit_test_barrage")
    svc.skip_dm(room_id)
    second = svc.act(room_id, ben, "shop_floor_fix")
    assert svc.dm_gate(room_id)["event_seq"] == second["event_seq"]
    svc.clear_dm_gate(room_id, first["event_seq"])
    assert svc.dm_gate(room_id)["waiting"] is True


def test_clearing_a_room_with_no_gate_is_harmless(rig):
    svc, _, room_id, _, _ = rig
    svc.clear_dm_gate(room_id, 99)          # no gate open; must not raise
    assert svc.dm_gate(room_id)["waiting"] is False
