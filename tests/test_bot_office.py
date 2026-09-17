"""Bots in the office: when they choose a place, and that they walk there."""
import pytest
from bots import (BOT_LINES, PLACE_BRANCHES, BotRunner, bot_line, choose_action,
                  decide)
from broker import EventBroker
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from engine.places import COFFEE_LIMIT
from service import GameService
from tests.test_bots import board


@pytest.fixture(scope="module")
def catalog():
    return load_catalog("data")


# --- the policy -------------------------------------------------------------

def test_no_scout_in_hand_sends_the_bot_to_the_lab(catalog):
    state = board("computer_scientist", ["refactor", "unit_test_barrage"],
                  revealed=[])
    assert decide(state, "p1", catalog) == ("bench_test", None, "lab")
    assert choose_action(state, "p1", catalog) == ("bench_test", None)


def test_a_scout_in_hand_is_preferred_to_the_lab(catalog):
    state = board("ee_engineer", ["signal_integrity_scan"], revealed=[])
    assert decide(state, "p1", catalog)[2] == "reveal"


def test_an_empty_hand_still_goes_to_the_lab(catalog):
    """The bench costs no Focus."""
    state = board("mechanical_engineer", ["fea_sweep"], focus=0, revealed=[])
    assert decide(state, "p1", catalog)[2] == "lab"


def test_a_known_weakness_keeps_the_bot_out_of_the_lab(catalog):
    state = board("computer_scientist", ["refactor", "unit_test_barrage"])
    assert decide(state, "p1", catalog)[2] == "attack"


def test_a_hurt_bot_with_no_heal_takes_a_coffee(catalog):
    state = board("computer_scientist", ["refactor", "unit_test_barrage"],
                  ally_stamina=4)
    state["characters"]["p1"]["stamina"] = 3
    assert decide(state, "p1", catalog) == ("coffee_break", None, "coffee")


def test_a_healthy_bot_does_not_drink_for_a_hurt_ally(catalog):
    """Coffee only helps the drinker; fall through to the ability policy."""
    state = board("computer_scientist", ["refactor", "unit_test_barrage"],
                  ally_stamina=4)
    assert decide(state, "p1", catalog)[2] == "attack"


def test_a_hurt_bot_that_can_heal_heals_instead(catalog):
    state = board("mechatronics_engineer",
                  ["sensor_fusion", "actuator_integration"], ally_stamina=4)
    state["characters"]["p1"]["stamina"] = 3
    assert decide(state, "p1", catalog)[2] == "heal"


def test_no_coffee_left_falls_through(catalog):
    state = board("computer_scientist", ["refactor", "unit_test_barrage"])
    state["characters"]["p1"].update(stamina=3, coffee_used=COFFEE_LIMIT)
    assert decide(state, "p1", catalog)[2] == "attack"


def test_the_policy_never_offers_a_place_out_of_turn(catalog):
    state = board("computer_scientist", ["refactor"], revealed=[])
    state["turn"]["turn_index"] = 1
    assert decide(state, "p1", catalog) == (None, None, "pass")


def test_deciding_mutates_nothing(catalog):
    state = board("computer_scientist", ["refactor"], revealed=[])
    before = repr(state)
    decide(state, "p1", catalog)
    assert repr(state) == before


def test_place_branches_have_their_own_lines():
    for branch in PLACE_BRANCHES:
        assert len(BOT_LINES[branch]) >= 2
        assert bot_line(branch, 0) != bot_line(branch, 1)
        assert bot_line(branch, 7) == BOT_LINES[branch][7 % len(BOT_LINES[branch])]


# --- at a real table --------------------------------------------------------

@pytest.fixture
def svc(tmp_path):
    return GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                       load_archetypes(), broker=EventBroker())


def _room(svc, *classes):
    room_id = svc.create_room("Kestrel", "aircraft")
    ids = [svc.add_bot(room_id, c)["player_id"] for c in classes]
    svc.start_game(room_id)
    return room_id, ids


def _events(svc, room_id, since=0):
    return [(e["kind"], e["actor"], e["payload"])
            for e in svc.events_since(room_id, since)]


def test_a_bot_walks_to_the_lab_and_bench_tests(svc):
    room_id, (bot, _) = _room(svc, "computer_scientist", "mechanical_technician")
    since = svc._room(room_id).latest_seq()
    assert BotRunner(svc, svc.catalog, delay=0.0).run_once() == 1
    log = _events(svc, room_id, since)
    kinds = [(k, a) for k, a, _ in log]
    move = kinds.index(("office_move", bot))
    act = kinds.index(("place_action", bot))
    assert move < act                                # walked, then acted
    assert log[move][2]["zone"] == "lab" and log[move][2]["is_bot"] is True
    assert log[act][2]["action"] == "bench_test"
    [chat] = [p for k, a, p in log if k == "chat"]
    assert chat["body"] in BOT_LINES["lab"]
    state = svc.snapshot(room_id)
    assert state["characters"][bot]["office_zone"] == "lab"


def test_a_hurt_bot_walks_to_the_break_room_for_coffee(svc):
    room_id, (bot, _) = _room(svc, "computer_scientist", "mechanical_technician")
    with svc._lock(room_id):
        room = svc._room(room_id)
        state = room.load_state()
        state["characters"][bot]["stamina"] = 2
        for hazard in state["hazards"]:
            hazard["revealed"] = ["weakness"]
        room.save_state(state)
    since = svc._room(room_id).latest_seq()
    BotRunner(svc, svc.catalog, delay=0.0).run_once()
    log = _events(svc, room_id, since)
    zones = [p["zone"] for k, a, p in log if k == "office_move" and a == bot]
    assert zones == ["break_room"]
    [act] = [p for k, a, p in log if k == "place_action"]
    assert act["action"] == "coffee_break"
    char = svc.snapshot(room_id)["characters"][bot]
    assert char["stamina"] > 2 and char["coffee_used"] == 1


def test_a_bot_using_an_ability_walks_to_its_desk_first(svc):
    room_id, (bot, _) = _room(svc, "ee_engineer", "mechanical_technician")
    since = svc._room(room_id).latest_seq()
    BotRunner(svc, svc.catalog, delay=0.0).run_once()
    log = _events(svc, room_id, since)
    kinds = [(k, a) for k, a, _ in log]
    assert kinds.index(("office_move", bot)) < kinds.index(("action", bot))
    move = next(p for k, a, p in log if k == "office_move")
    assert move["zone"] == f"desk:{bot}"


def test_a_bot_that_cannot_walk_still_plays(svc, monkeypatch):
    room_id, (bot, _) = _room(svc, "computer_scientist", "mechanical_technician")

    def boom(*args, **kwargs):
        raise RuntimeError("office closed")
    monkeypatch.setattr(svc, "office_move", boom)
    before = svc.snapshot(room_id)["turn"]["turn_index"]
    BotRunner(svc, svc.catalog, delay=0.0).run_once()
    # The bench refused (not in the lab), so the bot passed instead of wedging.
    assert svc.snapshot(room_id)["turn"]["turn_index"] != before
