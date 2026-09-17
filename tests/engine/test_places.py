"""engine.places: zones, desks, and the three place actions."""
import pytest
from engine.dice import Dice
from engine.effects import condition_total, expire_conditions
from engine.places import (COFFEE_LIMIT, DESK_DEFAULTS, PlaceError,
                           action_here, desk_assignments, desk_owner, desk_zone,
                           is_zone, normalise_desk, reset_phase, resolve_place,
                           rounds_until_next_turn, validate_place, zones)
from engine.rules import RuleError, advance_turn, resolve_action
from engine.classes import load_catalog
from tests.engine.test_effects import make_state


def at(state, pid, zone):
    state["characters"][pid]["office_zone"] = zone
    return state


# --- zones ------------------------------------------------------------------

def test_desks_follow_seat_order_and_zones_list_them():
    state = make_state()
    assert [d["player_id"] for d in desk_assignments(state)] == ["p1", "p2"]
    assert zones(state) == ["floor", "lab", "break_room", "whiteboard",
                            "desk:p1", "desk:p2"]


def test_is_zone_accepts_fixed_rooms_and_seated_desks_only():
    state = make_state()
    for zone in ("floor", "lab", "break_room", "whiteboard", "desk:p2"):
        assert is_zone(state, zone)
    for zone in ("desk:nobody", "desk:", "roof", "", None, 3, ["lab"]):
        assert not is_zone(state, zone)


def test_desk_owner_parses_only_desk_zones():
    assert desk_owner(desk_zone("p9")) == "p9"
    assert desk_owner("lab") is None
    assert desk_owner(None) is None


def test_action_here_names_what_the_zone_offers():
    state = make_state()
    assert action_here(state, "p1") is None                      # floor
    assert action_here(at(state, "p1", "lab"), "p1") == "bench_test"
    assert action_here(at(state, "p1", "break_room"), "p1") == "coffee_break"
    assert action_here(at(state, "p1", "desk:p1"), "p1") == "customize"
    assert action_here(at(state, "p1", "desk:p2"), "p1") == "pair_up"
    assert action_here(at(state, "p1", "whiteboard"), "p1") is None


# --- desks ------------------------------------------------------------------

def test_desk_customisation_falls_back_on_unknown_values():
    desk = normalise_desk({"monitor": "dual", "plant": "cactus", "mug": 7,
                           "poster": "gantt", "evil": "<script>"})
    assert desk == {**DESK_DEFAULTS, "monitor": "dual", "poster": "gantt"}
    assert normalise_desk(None) == DESK_DEFAULTS
    assert normalise_desk(["x"]) == DESK_DEFAULTS


# --- common checks ------------------------------------------------------------

def test_place_errors_are_rule_errors():
    assert issubclass(PlaceError, RuleError)


def test_unknown_action_is_rejected():
    with pytest.raises(PlaceError):
        validate_place(at(make_state(), "p1", "lab"), "p1", "nap")


def test_not_your_turn_is_rejected():
    state = at(make_state(), "p2", "lab")
    with pytest.raises(PlaceError, match="not your turn"):
        resolve_place(state, "p2", "bench_test")


def test_a_spectator_is_rejected():
    with pytest.raises(PlaceError, match="not seated"):
        validate_place(make_state(), "zz", "bench_test")


def test_burned_out_cannot_use_a_place():
    state = at(make_state(), "p1", "lab")
    state["characters"]["p1"]["stamina"] = 0
    with pytest.raises(PlaceError, match="Burned Out"):
        validate_place(state, "p1", "bench_test")


# --- bench_test ---------------------------------------------------------------

def test_bench_test_reveals_an_unrevealed_weakness():
    state = at(make_state(), "p1", "lab")
    changes = resolve_place(state, "p1", "bench_test")
    assert "weakness" in state["hazards"][0]["revealed"]
    assert changes == [{"kind": "reveal", "hazard_id": "h1", "fields": ["weakness"]}]
    assert state["conditions"] == []


def test_bench_test_with_the_weakness_known_gives_plus_two_to_the_next_roll():
    state = at(make_state(), "p1", "lab")
    state["hazards"][0]["revealed"] = ["weakness"]
    resolve_place(state, "p1", "bench_test")
    assert condition_total(state, "roll_bonus", "p1") == 2
    assert condition_total(state, "roll_bonus", "p2") == 0


def test_the_bench_bonus_survives_to_the_actors_next_roll_and_no_further():
    """Conditions tick at round end; the actor's next roll is after that tick."""
    state = at(make_state(), "p1", "lab")
    state["hazards"][0]["revealed"] = ["weakness"]
    resolve_place(state, "p1", "bench_test")
    advance_turn(state)                             # p2's turn
    assert advance_turn(state) is True              # round ends, back to p1
    expire_conditions(state)
    catalog = load_catalog("data")
    result = resolve_action(state, "p1", catalog.abilities["unit_test_barrage"],
                            Dice(3))
    assert result.roll_bonus >= 2
    advance_turn(state)
    advance_turn(state)
    expire_conditions(state)
    assert condition_total(state, "roll_bonus", "p1") == 0


def test_bench_test_outside_the_lab_is_rejected():
    for zone in ("floor", "break_room", "desk:p2", "whiteboard"):
        with pytest.raises(PlaceError, match="lab"):
            resolve_place(at(make_state(), "p1", zone), "p1", "bench_test")


# --- pair_up ------------------------------------------------------------------

def test_pair_up_buffs_both_actor_and_owner():
    state = at(make_state(), "p1", "desk:p2")
    resolve_place(state, "p1", "pair_up", "p2")
    assert condition_total(state, "roll_bonus", "p1") == 1
    assert condition_total(state, "roll_bonus", "p2") == 1


def test_pair_up_times_each_bonus_to_its_holders_next_roll():
    state = at(make_state(), "p1", "desk:p2")
    resolve_place(state, "p1", "pair_up")
    rounds = {c["target_id"]: c["rounds"] for c in state["conditions"]}
    assert rounds == {"p1": 2, "p2": 1}      # p2 still acts this round
    assert rounds_until_next_turn(state, "p1") == 2


def test_pair_up_once_per_round():
    state = at(make_state(), "p1", "desk:p2")
    resolve_place(state, "p1", "pair_up")
    with pytest.raises(PlaceError, match="once per round"):
        validate_place(state, "p1", "pair_up")
    state["turn"]["round"] += 1
    validate_place(state, "p1", "pair_up")


def test_pair_up_with_a_dead_owner_is_rejected():
    state = at(make_state(), "p1", "desk:p2")
    state["characters"]["p2"]["stamina"] = 0
    with pytest.raises(PlaceError, match="Burned Out"):
        validate_place(state, "p1", "pair_up")


def test_pair_up_at_your_own_desk_or_elsewhere_is_rejected():
    for zone in ("desk:p1", "lab", "floor"):
        with pytest.raises(PlaceError, match="colleague"):
            validate_place(at(make_state(), "p1", zone), "p1", "pair_up")


def test_pair_up_target_must_be_the_desk_owner():
    state = at(make_state(), "p1", "desk:p2")
    with pytest.raises(PlaceError):
        validate_place(state, "p1", "pair_up", "p1")


# --- coffee_break -------------------------------------------------------------

def test_coffee_restores_a_quarter_with_a_floor_of_two_capped_at_max():
    state = at(make_state(), "p1", "break_room")
    state["characters"]["p1"].update(stamina=3, max_stamina=10)
    resolve_place(state, "p1", "coffee_break")
    assert state["characters"]["p1"]["stamina"] == 5         # max(2, 10//4)
    state = at(make_state(), "p1", "break_room")
    state["characters"]["p1"].update(stamina=10, max_stamina=40)
    resolve_place(state, "p1", "coffee_break")
    assert state["characters"]["p1"]["stamina"] == 20        # 40//4
    state = at(make_state(), "p1", "break_room")
    state["characters"]["p1"].update(stamina=39, max_stamina=40)
    resolve_place(state, "p1", "coffee_break")
    assert state["characters"]["p1"]["stamina"] == 40


def test_coffee_is_limited_per_phase_and_reset_on_phase_advance():
    state = at(make_state(), "p1", "break_room")
    for _ in range(COFFEE_LIMIT):
        resolve_place(state, "p1", "coffee_break")
    with pytest.raises(PlaceError, match="coffee"):
        resolve_place(state, "p1", "coffee_break")
    reset_phase(state)
    resolve_place(state, "p1", "coffee_break")


def test_coffee_outside_the_break_room_is_rejected():
    with pytest.raises(PlaceError, match="break room"):
        resolve_place(at(make_state(), "p1", "lab"), "p1", "coffee_break")
