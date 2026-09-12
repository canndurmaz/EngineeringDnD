# tests/engine/test_phases.py
import pytest
from engine.classes import load_catalog
from engine.dice import Dice
from engine.phases import (PHASES, advance_phase, build_campaign,
                           check_end_conditions, load_archetypes,
                           load_hazard_templates, next_hazard_id, phase_cleared)
from tests.engine.test_effects import make_state


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


@pytest.fixture(scope="module")
def templates():
    return load_hazard_templates()


def test_five_phases_in_the_specified_order():
    assert [p[0] for p in PHASES] == [
        "requirements", "design", "prototype", "integration", "qualification"]


def test_archetypes_file_lists_at_least_the_eight_named_systems():
    archetypes = load_archetypes()
    ids = {a["id"] for a in archetypes}
    assert {"car", "aircraft", "weapon_platform", "spacecraft"} <= ids
    assert len(archetypes) >= 8


def test_every_phase_has_normal_templates_and_a_boss(templates):
    for phase_id, _ in PHASES:
        assert len(templates[phase_id]["normal"]) >= 3
        assert templates[phase_id]["boss"]["is_boss"] is True


def test_all_template_attack_types_are_valid(templates):
    valid = {"stress", "burn_budget", "burn_schedule", "debt"}
    for phase_id, _ in PHASES:
        entries = templates[phase_id]["normal"] + [templates[phase_id]["boss"]]
        assert all(e["attack_type"] in valid for e in entries)


def test_build_campaign_covers_every_phase(templates):
    hazards = build_campaign(Dice(3), templates)
    for index in range(5):
        assert any(h["phase_index"] == index for h in hazards)


def test_each_phase_has_two_or_three_normals_plus_one_boss(templates):
    hazards = build_campaign(Dice(3), templates)
    for index in range(5):
        in_phase = [h for h in hazards if h["phase_index"] == index]
        bosses = [h for h in in_phase if h["is_boss"]]
        assert len(bosses) == 1
        assert 2 <= len(in_phase) - 1 <= 3


def test_boss_is_always_last_in_its_phase(templates):
    hazards = build_campaign(Dice(3), templates)
    for index in range(5):
        in_phase = [h for h in hazards if h["phase_index"] == index]
        assert in_phase[-1]["is_boss"] is True


def test_severity_and_dc_scale_with_phase(templates):
    hazards = build_campaign(Dice(3), templates)
    first = [h for h in hazards if h["phase_index"] == 0 and not h["is_boss"]][0]
    last = [h for h in hazards if h["phase_index"] == 4 and not h["is_boss"]][0]
    assert last["max_severity"] > first["max_severity"]
    assert last["dc"] > first["dc"]


def test_hazard_ids_are_unique(templates):
    hazards = build_campaign(Dice(3), templates)
    assert len({h["id"] for h in hazards}) == len(hazards)


def test_build_campaign_is_deterministic_for_a_seed(templates):
    assert build_campaign(Dice(11), templates) == build_campaign(Dice(11), templates)


def test_next_hazard_id_returns_the_first_undefeated_in_phase(templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    first = next_hazard_id(s)
    for h in s["hazards"]:
        if h["id"] == first:
            h["defeated"] = True
    assert next_hazard_id(s) != first


def test_phase_cleared_only_when_all_phase_hazards_are_down(templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    assert phase_cleared(s) is False
    for h in s["hazards"]:
        if h["phase_index"] == 0:
            h["defeated"] = True
    assert phase_cleared(s) is True


def test_advance_phase_moves_forward_and_replenishes(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    s["party"]["budget"] = 50
    summary = advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert s["room"]["phase_index"] == 1
    assert s["party"]["budget"] == 60
    assert summary["phase"] == "design"


def test_advance_phase_levels_every_character(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert all(c["level"] == 2 for c in s["characters"].values())


def test_advance_phase_revives_burned_out_characters(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    s["characters"]["p2"]["stamina"] = 0
    advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert s["characters"]["p2"]["stamina"] > 0


def test_advance_phase_clears_lingering_conditions(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    s["conditions"] = [{"name": "roll_bonus", "scope": "party", "value": 3,
                        "rounds": 9, "target_id": None}]
    advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert s["conditions"] == []


def test_clearing_the_final_phase_wins(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    s["room"]["phase_index"] = 4
    for h in s["hazards"]:
        h["defeated"] = True
    advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert s["room"]["status"] == "won"
    assert check_end_conditions(s) == "win"


def test_zero_budget_loses():
    s = make_state()
    s["party"]["budget"] = 0
    assert check_end_conditions(s) == "lose_budget"


def test_negative_schedule_loses():
    s = make_state()
    s["party"]["schedule"] = -2
    assert check_end_conditions(s) == "lose_schedule"


def test_total_burnout_loses():
    s = make_state()
    for c in s["characters"].values():
        c["stamina"] = 0
    assert check_end_conditions(s) == "lose_burnout"


def test_partial_burnout_does_not_lose():
    s = make_state()
    s["characters"]["p2"]["stamina"] = 0
    assert check_end_conditions(s) is None


def test_healthy_game_has_no_end_condition():
    assert check_end_conditions(make_state()) is None
