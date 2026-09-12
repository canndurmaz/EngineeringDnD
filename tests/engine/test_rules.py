import pytest
from engine.classes import load_catalog
from engine.dice import Dice
from engine.effects import add_condition, condition_total
from engine.rules import (ActionResult, RuleError, advance_turn, effective_dc,
                          hazard_attack, resolve_action, stat_mod, validate_action)
from tests.engine.test_effects import make_state


class FixedDice(Dice):
    """Dice whose d20 returns a scripted sequence; other rolls stay seeded."""

    def __init__(self, naturals, seed=1):
        super().__init__(seed)
        self._naturals = list(naturals)

    def d20(self):
        return self._naturals.pop(0) if self._naturals else super().d20()


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_stat_mod_matches_dnd_table():
    assert [stat_mod(n) for n in (8, 10, 12, 14, 16, 18)] == [-1, 0, 1, 2, 3, 4]


def test_effective_dc_is_the_hazard_dc_by_default(cat):
    s = make_state()
    ability = cat.abilities["refactor"]        # dc_mod +1
    assert effective_dc(s, ability, "p1") == 12 + 1


def test_tech_debt_raises_dc_one_per_ten_points(cat):
    s = make_state()
    s["party"]["tech_debt"] = 25
    ability = cat.abilities["unit_test_barrage"]   # dc_mod -1
    assert effective_dc(s, ability, "p1") == 12 - 1 + 2


def test_dc_delta_condition_lowers_the_dc(cat):
    s = make_state()
    add_condition(s, "dc_delta", "party", -3, 1)
    ability = cat.abilities["refactor"]
    assert effective_dc(s, ability, "p1") == 12 + 1 - 3


def test_success_when_total_meets_the_dc(cat):
    s = make_state()
    ability = cat.abilities["refactor"]  # RIGOR 16 -> +3, dc 13
    result = resolve_action(s, "p1", ability, FixedDice([10]))
    assert result.total == 13 and result.outcome == "success"


def test_failure_when_total_is_below_the_dc(cat):
    s = make_state()
    result = resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([5]))
    assert result.outcome == "failure"


def test_natural_twenty_is_a_crit_and_doubles_effects(cat):
    s = make_state()
    result = resolve_action(s, "p1", cat.abilities["unit_test_barrage"],
                            FixedDice([20]))
    assert result.outcome == "crit"


def test_natural_one_is_a_fumble(cat):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("descope")
    result = resolve_action(s, "p1", cat.abilities["descope"], FixedDice([1]))
    assert result.outcome == "fumble"


def test_fumble_triggers_an_immediate_hazard_attack(cat):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("descope")
    before = s["characters"]["p1"]["stamina"]
    resolve_action(s, "p1", cat.abilities["descope"], FixedDice([1]))
    assert s["characters"]["p1"]["stamina"] < before


def test_no_fumble_ability_never_fumbles(cat):
    s = make_state()
    result = resolve_action(s, "p1", cat.abilities["unit_test_barrage"],
                            FixedDice([1]))
    assert result.outcome == "failure"


def test_crit_range_condition_makes_nineteen_a_crit(cat):
    s = make_state()
    add_condition(s, "crit_range", "party", 19, 2)
    result = resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([19]))
    assert result.outcome == "crit"


def test_fixed_roll_ability_ignores_the_die(cat):
    s = make_state()
    s["characters"]["p1"]["class_id"] = "control_systems_engineer"
    s["characters"]["p1"]["unlocked"] = ["kalman_filter"]
    s["characters"]["p1"]["focus"] = 4
    result = resolve_action(s, "p1", cat.abilities["kalman_filter"], FixedDice([20]))
    assert result.natural == 11 and result.outcome != "crit"


def test_cancel_fumble_condition_converts_a_fumble_to_a_failure(cat):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("descope")
    add_condition(s, "cancel_fumble", "party", 1, 3)
    result = resolve_action(s, "p1", cat.abilities["descope"], FixedDice([1]))
    assert result.outcome == "failure"
    assert condition_total(s, "cancel_fumble", "p1") == 0


def test_reroll_condition_is_consumed_on_a_failed_roll(cat):
    s = make_state()
    add_condition(s, "reroll", "ally", 1, 2, "p1")
    result = resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([2, 18]))
    assert result.rerolled is True and result.natural == 18
    assert condition_total(s, "reroll", "p1") == 0


def test_reroll_is_not_used_when_the_first_roll_succeeds(cat):
    s = make_state()
    add_condition(s, "reroll", "ally", 1, 2, "p1")
    result = resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([18, 2]))
    assert result.rerolled is False
    assert condition_total(s, "reroll", "p1") == 1


def test_stat_alt_uses_the_higher_modifier(cat):
    s = make_state()
    s["characters"]["p1"]["class_id"] = "mechatronics_engineer"
    s["characters"]["p1"]["unlocked"] = ["sensor_fusion"]
    s["characters"]["p1"]["stats"]["CRAFT"] = 18   # beats SYSTEMS 12
    result = resolve_action(s, "p1", cat.abilities["sensor_fusion"], FixedDice([10]))
    assert result.stat_used == "CRAFT" and result.stat_mod == 4


def test_focus_is_spent_on_use(cat):
    s = make_state()
    resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([15]))
    assert s["characters"]["p1"]["focus"] == 2   # 4 - 2


def test_insufficient_focus_is_rejected(cat):
    s = make_state()
    s["characters"]["p1"]["focus"] = 1
    with pytest.raises(RuleError, match="Focus"):
        validate_action(s, "p1", cat.abilities["refactor"])


def test_locked_ability_is_rejected(cat):
    s = make_state()
    with pytest.raises(RuleError, match="not unlocked"):
        validate_action(s, "p1", cat.abilities["rubber_duck"])


def test_acting_out_of_turn_is_rejected(cat):
    s = make_state()  # turn_index 0 -> p1 is active
    with pytest.raises(RuleError, match="turn"):
        validate_action(s, "p2", cat.abilities["shop_floor_fix"])


def test_burned_out_character_cannot_act(cat):
    s = make_state()
    s["characters"]["p1"]["stamina"] = 0
    with pytest.raises(RuleError, match="Burned Out"):
        validate_action(s, "p1", cat.abilities["refactor"])


def test_extra_budget_cost_is_charged_and_checked(cat):
    s = make_state()
    s["characters"]["p1"]["class_id"] = "ee_engineer"
    s["characters"]["p1"]["unlocked"] = ["board_respin"]
    resolve_action(s, "p1", cat.abilities["board_respin"], FixedDice([15]))
    assert s["party"]["budget"] == 85


def test_extra_budget_cost_is_rejected_when_unaffordable(cat):
    s = make_state()
    s["party"]["budget"] = 5
    s["characters"]["p1"]["class_id"] = "ee_engineer"
    s["characters"]["p1"]["unlocked"] = ["board_respin"]
    with pytest.raises(RuleError, match="Budget"):
        validate_action(s, "p1", cat.abilities["board_respin"])


def test_once_per_hazard_ability_is_rejected_on_reuse(cat):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("binary_search_debug")
    s["characters"]["p1"]["focus"] = 6
    resolve_action(s, "p1", cat.abilities["binary_search_debug"], FixedDice([18]))
    with pytest.raises(RuleError, match="once"):
        validate_action(s, "p1", cat.abilities["binary_search_debug"])


def test_borrowed_ability_condition_permits_a_locked_ability(cat):
    s = make_state()
    add_condition(s, "borrowed_ability", "ally", 1, 2, "p1")
    validate_action(s, "p1", cat.abilities["shop_floor_fix"])   # must not raise


def test_hazard_stress_attack_damages_a_character(cat):
    s = make_state()
    before = sum(c["stamina"] for c in s["characters"].values())
    hazard_attack(s, Dice(3))
    assert sum(c["stamina"] for c in s["characters"].values()) < before


def test_shield_absorbs_hazard_stress(cat):
    s = make_state()
    add_condition(s, "shield", "party", 99, 2)
    before = sum(c["stamina"] for c in s["characters"].values())
    hazard_attack(s, Dice(3))
    assert sum(c["stamina"] for c in s["characters"].values()) == before


def test_burn_budget_attack_drains_budget(cat):
    s = make_state()
    s["hazards"][0]["attack_type"] = "burn_budget"
    hazard_attack(s, Dice(3))
    assert s["party"]["budget"] < 100


def test_resist_burn_condition_blocks_a_burn_attack(cat):
    s = make_state()
    s["hazards"][0]["attack_type"] = "burn_schedule"
    add_condition(s, "resist_burn", "party", 1, 2)
    hazard_attack(s, Dice(3))
    assert s["party"]["schedule"] == 100


def test_no_debt_condition_blocks_a_debt_attack(cat):
    s = make_state()
    s["hazards"][0]["attack_type"] = "debt"
    add_condition(s, "no_debt", "hazard", 1, 2)
    hazard_attack(s, Dice(3))
    assert s["party"]["tech_debt"] == 0


def test_stunned_hazard_skips_its_attack(cat):
    s = make_state()
    add_condition(s, "stunned", "hazard", 1, 1)
    before = sum(c["stamina"] for c in s["characters"].values())
    hazard_attack(s, Dice(3))
    assert sum(c["stamina"] for c in s["characters"].values()) == before


def test_advance_turn_cycles_and_reports_round_completion(cat):
    s = make_state()
    assert advance_turn(s) is False      # p1 -> p2
    assert s["turn"]["turn_index"] == 1
    assert advance_turn(s) is True       # p2 -> wraps, round complete
    assert s["turn"]["round"] == 2 and s["turn"]["turn_index"] == 0


def test_advance_turn_skips_burned_out_players(cat):
    s = make_state()
    s["characters"]["p2"]["stamina"] = 0
    advance_turn(s)
    assert s["turn"]["order"][s["turn"]["turn_index"]] == "p1"


def test_focus_regenerates_one_per_turn(cat):
    s = make_state()
    s["characters"]["p2"]["focus"] = 0
    advance_turn(s)
    assert s["characters"]["p2"]["focus"] == 1
