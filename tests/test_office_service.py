"""GameService's office: moving, place actions, desks -- and the rules they
share with abilities (the lock, the turn, the DM gate, the narration)."""
import pytest
from broker import EventBroker
from engine.classes import load_catalog
from engine.effects import condition_total
from engine.phases import load_archetypes, load_hazard_templates
from engine.places import COFFEE_LIMIT, DESK_DEFAULTS
from engine.rules import RuleError
from narrator.queue_ import NarrationQueue
from service import GameService, ServiceError


def _svc(tmp_path, queue=None):
    return GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                       load_archetypes(), queue=queue, broker=EventBroker())


@pytest.fixture
def svc(tmp_path):
    return _svc(tmp_path)


def seat_two(svc, start=True):
    room_id = svc.create_room("Kestrel", "aircraft")
    a = svc.join_room(room_id, "Ada", "computer_scientist")["player_id"]
    b = svc.join_room(room_id, "Ben", "mechanical_technician")["player_id"]
    if start:
        svc.start_game(room_id)
    return room_id, a, b


def sturdy(svc, room_id, *pids):
    """The hazard hits back at every round end, and the room seed is random.
    Tests that cross a round keep everyone standing so a lucky hit cannot
    burn someone out and reorder the turn."""
    for pid in pids:
        svc._set_character(room_id, pid, stamina=500, max_stamina=500)


def kinds(result):
    return [e["kind"] for e in result["events"]]


def active(svc, room_id):
    turn = svc.snapshot(room_id)["turn"]
    return turn["order"][turn["turn_index"]]


def weakness_known(svc, room_id):
    state = svc.snapshot(room_id)
    hazard = next(h for h in state["hazards"] if h["id"] == state["active_hazard_id"])
    return "weakness" in hazard["revealed"]


# --- move -------------------------------------------------------------------

def test_move_stores_the_zone_and_logs_an_office_move(svc):
    room_id, ada, _ = seat_two(svc)
    q = svc.broker.subscribe(room_id)
    assert svc.office_move(room_id, ada, "lab") == {"zone": "lab", "moved": True}
    assert svc.snapshot(room_id)["characters"][ada]["office_zone"] == "lab"
    item = q.get_nowait()
    assert item["kind"] == "office_move"
    assert item["zone"] == "lab" and item["from"] == "floor"
    assert item["player_id"] == ada and item["is_bot"] is False


def test_move_costs_no_turn(svc):
    room_id, ada, _ = seat_two(svc)
    before = svc.snapshot(room_id)["turn"]
    svc.office_move(room_id, ada, "break_room")
    svc.office_move(room_id, ada, "desk:" + ada)
    assert svc.snapshot(room_id)["turn"] == before


def test_moving_to_the_same_zone_logs_nothing(svc):
    room_id, ada, _ = seat_two(svc)
    svc.office_move(room_id, ada, "lab")
    seq = svc._room(room_id).latest_seq()
    assert svc.office_move(room_id, ada, "lab")["moved"] is False
    assert svc._room(room_id).latest_seq() == seq


def test_unknown_zone_is_rejected(svc):
    room_id, ada, _ = seat_two(svc)
    for zone in ("roof", "desk:nobody", "", "desk:"):
        with pytest.raises(ServiceError, match="no such place"):
            svc.office_move(room_id, ada, zone)


def test_a_stranger_cannot_move(svc):
    room_id, _, _ = seat_two(svc)
    with pytest.raises(ServiceError, match="not seated"):
        svc.office_move(room_id, "pnobody", "lab")


def test_walking_is_allowed_while_the_dm_writes(tmp_path):
    svc = _svc(tmp_path, NarrationQueue())
    room_id, ada, ben = seat_two(svc)
    svc.act(room_id, ada, "unit_test_barrage")
    assert svc.dm_gate(room_id)["waiting"]
    assert svc.office_move(room_id, ben, "lab")["moved"]


# --- bench_test -------------------------------------------------------------

def test_bench_test_reveals_the_weakness_and_passes_the_turn(svc):
    room_id, ada, ben = seat_two(svc)
    svc.office_move(room_id, ada, "lab")
    result = svc.office_act(room_id, ada, "bench_test")
    assert result["changes"][0]["kind"] == "reveal"
    assert "place_action" in kinds(result)
    assert weakness_known(svc, room_id)
    assert active(svc, room_id) == ben


def test_bench_test_with_the_weakness_known_buffs_the_actor(svc):
    room_id, ada, ben = seat_two(svc)
    sturdy(svc, room_id, ada, ben)
    svc.office_move(room_id, ada, "lab")
    svc.office_act(room_id, ada, "bench_test")
    svc.end_turn(room_id, ben)                       # round ends
    svc.office_act(room_id, ada, "bench_test")
    assert condition_total(svc.snapshot(room_id), "roll_bonus", ada) == 2


def test_bench_test_from_the_wrong_zone_is_rejected(svc):
    room_id, ada, _ = seat_two(svc)
    with pytest.raises(RuleError, match="lab"):
        svc.office_act(room_id, ada, "bench_test")
    assert active(svc, room_id) == ada


def test_place_action_out_of_turn_is_rejected(svc):
    room_id, _, ben = seat_two(svc)
    svc.office_move(room_id, ben, "lab")
    with pytest.raises(RuleError, match="not your turn"):
        svc.office_act(room_id, ben, "bench_test")


def test_place_action_before_the_game_starts_is_rejected(svc):
    room_id, ada, _ = seat_two(svc, start=False)
    svc.office_move(room_id, ada, "lab")
    with pytest.raises(ServiceError, match="not started"):
        svc.office_act(room_id, ada, "bench_test")


def test_unknown_place_action_is_rejected(svc):
    room_id, ada, _ = seat_two(svc)
    with pytest.raises(ServiceError, match="unknown place action"):
        svc.office_act(room_id, ada, "customize")


def test_place_actions_respect_the_dm_gate_and_open_it(tmp_path):
    queue = NarrationQueue()
    svc = _svc(tmp_path, queue)
    room_id, ada, ben = seat_two(svc)
    svc.office_move(room_id, ada, "lab")
    result = svc.office_act(room_id, ada, "bench_test")
    gate = svc.dm_gate(room_id)
    assert gate == {"waiting": True, "event_seq": result["event_seq"]}
    jobs = []
    while (job := queue.get(timeout=0.05)) is not None:
        jobs.append(job)
    [job] = [j for j in jobs if j["kind"] == "turn"]
    assert job["event_seq"] == result["event_seq"]
    assert job["ability_name"] == "Bench Test" and job["outcome"] == "success"
    assert svc._room(room_id).narration(result["event_seq"])["status"] == "pending"

    svc.office_move(room_id, ben, "break_room")
    svc._set_character(room_id, ben, stamina=1)
    with pytest.raises(ServiceError, match="DM is still writing"):
        svc.office_act(room_id, ben, "coffee_break")
    svc.clear_dm_gate(room_id, result["event_seq"])
    svc.office_act(room_id, ben, "coffee_break")


# --- pair_up ----------------------------------------------------------------

def test_pair_up_buffs_both_and_is_logged_with_the_partner(svc):
    room_id, ada, ben = seat_two(svc)
    svc.office_move(room_id, ada, "desk:" + ben)
    result = svc.office_act(room_id, ada, "pair_up", ben)
    event = next(e for e in result["events"] if e["kind"] == "place_action")
    assert event["payload"]["partner_id"] == ben
    state = svc.snapshot(room_id)
    assert condition_total(state, "roll_bonus", ada) == 1
    assert condition_total(state, "roll_bonus", ben) == 1


def test_pair_up_is_once_per_round(svc):
    """Force a second turn in the same round to prove the limit holds."""
    room_id, ada, ben = seat_two(svc)
    svc.office_move(room_id, ada, "desk:" + ben)
    svc.office_act(room_id, ada, "pair_up")
    with svc._lock(room_id):
        room = svc._room(room_id)
        state = room.load_state()
        state["turn"]["turn_index"] = 0
        room.save_state(state)
    with pytest.raises(RuleError, match="once per round"):
        svc.office_act(room_id, ada, "pair_up")


def test_pair_up_with_a_burned_out_owner_is_rejected(svc):
    room_id, ada, ben = seat_two(svc)
    svc._set_character(room_id, ben, stamina=0)
    svc.office_move(room_id, ada, "desk:" + ben)
    with pytest.raises(RuleError, match="Burned Out"):
        svc.office_act(room_id, ada, "pair_up")


def test_pair_up_at_your_own_desk_is_rejected(svc):
    room_id, ada, _ = seat_two(svc)
    svc.office_move(room_id, ada, "desk:" + ada)
    with pytest.raises(RuleError, match="colleague"):
        svc.office_act(room_id, ada, "pair_up")


# --- coffee_break -----------------------------------------------------------

def _coffee_turn(svc, room_id, ada, ben):
    svc.office_act(room_id, ada, "coffee_break")
    svc.end_turn(room_id, ben)


def test_coffee_restores_stamina(svc):
    room_id, ada, _ = seat_two(svc)
    max_stamina = svc.snapshot(room_id)["characters"][ada]["max_stamina"]
    svc._set_character(room_id, ada, stamina=1)
    svc.office_move(room_id, ada, "break_room")
    svc.office_act(room_id, ada, "coffee_break")
    char = svc.snapshot(room_id)["characters"][ada]
    assert char["stamina"] == min(max_stamina, 1 + max(2, max_stamina // 4))
    assert char["coffee_used"] == 1


def test_coffee_is_limited_per_phase_and_resets_on_phase_advance(svc):
    room_id, ada, ben = seat_two(svc)
    sturdy(svc, room_id, ada, ben)                   # coffee is allowed at full
    svc.office_move(room_id, ada, "break_room")
    for _ in range(COFFEE_LIMIT):
        _coffee_turn(svc, room_id, ada, ben)
    with pytest.raises(RuleError, match="coffee"):
        svc.office_act(room_id, ada, "coffee_break")
    svc._force_clear_phase(room_id)
    state = svc.snapshot(room_id)
    assert state["room"]["phase_index"] == 1
    assert not state["characters"][ada].get("coffee_used")
    assert active(svc, room_id) == ada
    svc.office_act(room_id, ada, "coffee_break")


def test_coffee_outside_the_break_room_is_rejected(svc):
    room_id, ada, _ = seat_two(svc)
    svc.office_move(room_id, ada, "lab")
    with pytest.raises(RuleError, match="break room"):
        svc.office_act(room_id, ada, "coffee_break")


def test_a_place_action_can_end_the_round(svc):
    room_id, ada, ben = seat_two(svc)
    svc.end_turn(room_id, ada)
    svc.office_move(room_id, ben, "lab")
    result = svc.office_act(room_id, ben, "bench_test")
    assert "hazard_attack" in kinds(result)
    assert svc.snapshot(room_id)["turn"]["round"] == 2


# --- desks ------------------------------------------------------------------

def test_desk_is_saved_normalised_and_costs_no_turn(svc):
    room_id, ada, ben = seat_two(svc)
    before = svc.snapshot(room_id)["turn"]
    q = svc.broker.subscribe(room_id)
    out = svc.set_desk(room_id, ben, {"monitor": "laptop", "plant": "<img>",
                                      "mug": "tea", "hack": 1})
    assert out["desk"] == {**DESK_DEFAULTS, "monitor": "laptop", "mug": "tea"}
    state = svc.snapshot(room_id)
    assert state["characters"][ben]["desk"] == out["desk"]
    assert state["turn"] == before
    assert q.get_nowait()["kind"] == "desk_updated"


def test_desk_can_be_set_in_the_lobby(svc):
    room_id, ada, _ = seat_two(svc, start=False)
    assert svc.set_desk(room_id, ada, {"poster": "gantt"})["desk"]["poster"] == "gantt"


def test_a_stranger_cannot_set_a_desk(svc):
    room_id, _, _ = seat_two(svc)
    with pytest.raises(ServiceError, match="not seated"):
        svc.set_desk(room_id, "pnobody", {})


def test_the_office_block_lists_zones_and_desks(svc):
    room_id, ada, ben = seat_two(svc)
    block = svc.office(svc.snapshot(room_id))
    assert block["zones"] == ["floor", "lab", "break_room", "whiteboard",
                              f"desk:{ada}", f"desk:{ben}"]
    assert [d["player_id"] for d in block["desks"]] == [ada, ben]
    assert block["positions"] == {ada: "floor", ben: "floor"}
