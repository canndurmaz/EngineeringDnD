"""Gate-boss special rules (spec 2.6): each one fires in its own situation,
and in no other.

Every test here puts the same party in front of the same problem twice -- once
with the rule on the hazard and once without -- because the whole value of a
special rule is the difference it makes, and a test that only asserts the
penalised case cannot tell a rule from a coincidence.
"""
import pytest
from engine.classes import load_catalog
from engine.dice import Dice
from engine.hazard_rules import (damage_floor, escalate, neutralises,
                                 penalised_stats, roll_penalty, rule_of)
from engine.phases import build_campaign, load_hazard_templates
from engine.rules import end_of_round, resolve_action
from tests.engine.test_effects import make_state
from tests.engine.test_rules import FixedDice

RULES = {
    "System Requirements Review": "no_descope",
    "Preliminary Design Review": "rigor_only",
    "First Article Inspection": "hands_on",
    "Test Readiness Review": "escalating",
    "Qualification Test Campaign": "focused_fire",
}


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


@pytest.fixture(scope="module")
def templates():
    return load_hazard_templates()


def with_rule(state, rule):
    """The board's one hazard, carrying `rule`."""
    state["hazards"][0]["rule"] = rule
    return state


def rule(rule_id, **params):
    return {"id": rule_id, "text": f"{rule_id} applies here.", "params": params}


# --- the data ---------------------------------------------------------------

def test_every_gate_boss_carries_exactly_one_special_rule(templates):
    for phase in templates.values():
        boss = phase["boss"]
        assert boss.get("rule"), f"{boss['name']} has no special rule"
        assert RULES[boss["name"]] == boss["rule"]["id"]


def test_every_rule_reads_in_one_line(templates):
    for phase in templates.values():
        text = phase["boss"]["rule"]["text"]
        assert text.strip() and "\n" not in text and len(text) <= 90, text


def test_no_ordinary_problem_carries_a_rule(templates):
    for phase in templates.values():
        for normal in phase["normal"]:
            assert "rule" not in normal


def test_a_built_campaign_puts_the_rule_on_the_boss_and_nowhere_else(templates):
    hazards = build_campaign(Dice(7), templates)
    for hazard in hazards:
        if hazard["is_boss"]:
            assert rule_of(hazard)["id"] == RULES[hazard["name"]]
        else:
            assert "rule" not in hazard


def test_an_unknown_rule_id_is_inert_rather_than_fatal():
    hazard = {"rule": {"id": "from_a_newer_build", "params": {"stats": ["CRAFT"]}}}
    assert rule_of(hazard) == {}
    assert penalised_stats(hazard) == ()
    assert roll_penalty(hazard, "CRAFT") == 0
    assert damage_floor(hazard, 2) == 2


# --- no_descope -------------------------------------------------------------

def _descope_board(cat, rule_id=None):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("descope")
    if rule_id:
        with_rule(s, rule(rule_id))
    return s


def test_no_descope_neutralises_a_debt_adding_ability(cat):
    s = _descope_board(cat, "no_descope")
    before = s["hazards"][0]["severity"]
    result = resolve_action(s, "p1", cat.abilities["descope"], FixedDice([20]))
    assert s["hazards"][0]["severity"] == before      # the 10 damage never lands
    assert s["party"]["tech_debt"] == 0               # and neither does the debt
    assert [c["kind"] for c in result.changes] == ["rule_blocked"]


def test_no_descope_still_spends_the_focus(cat):
    """The turn is the punishment. The move is refused, not refunded."""
    s = _descope_board(cat, "no_descope")
    before = s["characters"]["p1"]["focus"]
    resolve_action(s, "p1", cat.abilities["descope"], FixedDice([20]))
    assert s["characters"]["p1"]["focus"] == before - 1


def test_the_same_ability_works_normally_at_a_hazard_without_the_rule(cat):
    s = _descope_board(cat)
    before = s["hazards"][0]["severity"]
    resolve_action(s, "p1", cat.abilities["descope"], FixedDice([20]))
    assert s["hazards"][0]["severity"] < before
    assert s["party"]["tech_debt"] > 0


def test_no_descope_leaves_an_ability_that_adds_no_debt_alone(cat):
    s = make_state()
    with_rule(s, rule("no_descope"))
    before = s["hazards"][0]["severity"]
    resolve_action(s, "p1", cat.abilities["unit_test_barrage"], FixedDice([18]))
    assert s["hazards"][0]["severity"] < before


def test_neutralises_reads_the_ability_not_the_roll(cat):
    debt = cat.abilities["solder_bodge"]
    clean = cat.abilities["harness_rework"]
    boss = {"rule": rule("no_descope")}
    assert neutralises(boss, debt) and not neutralises(boss, clean)
    assert not neutralises({"rule": rule("focused_fire")}, debt)


# --- rigor_only and hands_on ------------------------------------------------

@pytest.mark.parametrize("rule_id,punished,spared", [
    ("rigor_only", ("CRAFT", "GRIT"), ("RIGOR", "SYSTEMS", "COMMS", "INTUITION")),
    ("hands_on", ("RIGOR", "SYSTEMS"), ("CRAFT", "GRIT", "COMMS", "INTUITION")),
])
def test_a_stat_rule_penalises_its_own_stats_and_no_others(rule_id, punished, spared):
    hazard = {"rule": rule(rule_id, stats=list(punished), penalty=4)}
    for stat in punished:
        assert roll_penalty(hazard, stat) == -4, stat
    for stat in spared:
        assert roll_penalty(hazard, stat) == 0, stat


def test_the_penalty_lands_on_the_roll_the_player_can_see(cat):
    """A -4 on the roll, not a moved DC: the action line has to explain itself."""
    s = make_state()
    with_rule(s, rule("rigor_only", stats=["CRAFT", "GRIT"], penalty=4))
    s["characters"]["p2"]["unlocked"].append("torque_to_spec")
    s["turn"]["turn_index"] = 1
    result = resolve_action(s, "p2", cat.abilities["torque_to_spec"], FixedDice([12]))
    assert result.stat_used == "GRIT" and result.roll_bonus == -4
    assert result.dc == 12 - 1                    # the hazard's DC has not moved


def test_a_spared_stat_rolls_at_no_penalty_under_the_same_rule(cat):
    s = make_state()
    with_rule(s, rule("rigor_only", stats=["CRAFT", "GRIT"], penalty=4))
    result = resolve_action(s, "p1", cat.abilities["unit_test_barrage"], FixedDice([12]))
    assert result.stat_used == "RIGOR" and result.roll_bonus == 0


def test_hands_on_is_the_mirror_of_rigor_only(cat):
    s = make_state()
    with_rule(s, rule("hands_on", stats=["RIGOR", "SYSTEMS"], penalty=4))
    result = resolve_action(s, "p1", cat.abilities["unit_test_barrage"], FixedDice([12]))
    assert result.roll_bonus == -4


# --- escalating -------------------------------------------------------------

def test_escalating_raises_the_dc_once_per_round():
    s = make_state()
    with_rule(s, rule("escalating", per_round=1))
    base = s["hazards"][0]["dc"]
    for round_number in range(1, 4):
        change = end_of_round(s)
        assert change["amount"] == 1
        assert s["hazards"][0]["dc"] == base + round_number
        assert change["dc"] == s["hazards"][0]["dc"]


def test_escalating_stops_once_the_hazard_is_beaten():
    s = make_state()
    with_rule(s, rule("escalating", per_round=1))
    s["hazards"][0]["defeated"] = True
    assert end_of_round(s) is None
    assert escalate(s["hazards"][0]) == 0


def test_escalation_does_not_carry_to_the_next_hazard():
    """Each hazard escalates its own DC, so the next one starts at its own."""
    s = make_state()
    with_rule(s, rule("escalating", per_round=1))
    fresh = dict(s["hazards"][0], id="h2", dc=12)
    fresh.pop("rule")
    s["hazards"].append(fresh)
    for _ in range(5):
        end_of_round(s)
    assert s["hazards"][0]["dc"] == 17
    s["active_hazard_id"] = "h2"
    assert end_of_round(s) is None
    assert s["hazards"][1]["dc"] == 12


def test_a_hazard_without_the_rule_never_escalates():
    s = make_state()
    assert end_of_round(s) is None
    assert s["hazards"][0]["dc"] == 12


# --- focused_fire -----------------------------------------------------------

def test_focused_fire_floors_a_small_hit_at_one():
    hazard = {"rule": rule("focused_fire", threshold=8, floor=1)}
    assert [damage_floor(hazard, n) for n in (1, 4, 7)] == [1, 1, 1]


def test_focused_fire_leaves_a_real_hit_intact():
    hazard = {"rule": rule("focused_fire", threshold=8, floor=1)}
    assert [damage_floor(hazard, n) for n in (8, 9, 40)] == [8, 9, 40]


def test_focused_fire_does_not_invent_damage_out_of_a_miss():
    hazard = {"rule": rule("focused_fire", threshold=8, floor=1)}
    assert damage_floor(hazard, 0) == 0


def test_focused_fire_reaches_real_damage_through_the_engine(cat):
    """The chip ability lands for 1; the expensive one lands for what it rolled."""
    s = make_state()
    with_rule(s, rule("focused_fire", threshold=8, floor=1))
    s["characters"]["p2"]["unlocked"].append("shop_floor_fix")
    s["turn"]["turn_index"] = 1
    before = s["hazards"][0]["severity"]
    resolve_action(s, "p2", cat.abilities["shop_floor_fix"], FixedDice([19]))
    assert before - s["hazards"][0]["severity"] == 1

    s2 = make_state()
    with_rule(s2, rule("focused_fire", threshold=8, floor=1))
    s2["characters"]["p1"]["unlocked"].append("fea_deep_dive")
    before = s2["hazards"][0]["severity"]
    resolve_action(s2, "p1", cat.abilities["fea_deep_dive"], FixedDice([19]))
    assert before - s2["hazards"][0]["severity"] >= 8


def test_a_hazard_without_focused_fire_takes_the_chip_damage_it_is_given(cat):
    s = make_state()
    s["characters"]["p2"]["unlocked"].append("shop_floor_fix")
    s["turn"]["turn_index"] = 1
    before = s["hazards"][0]["severity"]
    resolve_action(s, "p2", cat.abilities["shop_floor_fix"], FixedDice([19]))
    assert 1 <= before - s["hazards"][0]["severity"] <= 4


# --- one rule does not do another rule's job --------------------------------

@pytest.mark.parametrize("rule_id", sorted(RULES.values()))
def test_a_rule_only_does_its_own_job(rule_id, cat):
    hazard = {"rule": rule(rule_id, stats=["CRAFT", "GRIT"], penalty=4,
                           per_round=1, threshold=8, floor=1),
              "dc": 12, "defeated": False}
    assert (roll_penalty(hazard, "CRAFT") != 0) == (rule_id in ("rigor_only", "hands_on"))
    assert neutralises(hazard, cat.abilities["descope"]) == (rule_id == "no_descope")
    assert (damage_floor(hazard, 3) == 1) == (rule_id == "focused_fire")
    assert (escalate(hazard) == 1) == (rule_id == "escalating")
