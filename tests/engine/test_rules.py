import json

import pytest
from engine.classes import load_catalog
from engine.dice import Dice
from engine.effects import (active_conditions, add_condition,
                            condition_total)
from engine.rules import (ActionResult, RuleError, advance_turn, effective_dc,
                          hazard_attack, hazard_attack_count, resolve_action,
                          stat_mod, validate_action)
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


def test_a_landed_attack_reveals_next_attack(cat):
    """The payload names the attack type, so the reveal state must admit it."""
    s = make_state()
    assert "next_attack" not in s["hazards"][0].get("revealed", [])
    result = hazard_attack(s, Dice(3))
    assert result["attack"] == "stress"
    assert "next_attack" in s["hazards"][0]["revealed"]


def test_revealing_next_attack_keeps_other_reveals(cat):
    s = make_state()
    s["hazards"][0]["revealed"] = ["weakness"]
    hazard_attack(s, Dice(3))
    assert set(s["hazards"][0]["revealed"]) == {"weakness", "next_attack"}


def test_a_stunned_hazard_reveals_nothing(cat):
    """No attack happened, so the party learned nothing about the next one."""
    s = make_state()
    add_condition(s, "stunned", "hazard", 1, 1)
    hazard_attack(s, Dice(3))
    assert "next_attack" not in s["hazards"][0].get("revealed", [])


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


# --- the hazard acts in proportion to the living party ---------------------

def _party_of(size):
    """A state whose party has `size` identical, living characters."""
    s = make_state()
    template = s["characters"]["p1"]
    s["characters"] = {}
    s["turn"]["order"] = []
    for n in range(size):
        pid = f"q{n}"
        char = json.loads(json.dumps(template))
        char["player_id"], char["name"] = pid, f"Q{n}"
        char["stamina"] = char["max_stamina"] = 40   # deep enough to survive a round
        s["characters"][pid] = char
        s["turn"]["order"].append(pid)
    return s


def test_attack_count_scales_with_party_size(cat):
    assert hazard_attack_count(_party_of(1)) == 1
    assert hazard_attack_count(_party_of(4)) == 1
    assert hazard_attack_count(_party_of(5)) == 2
    assert hazard_attack_count(_party_of(8)) == 2
    assert hazard_attack_count(_party_of(9)) == 3


def test_a_solo_party_still_faces_one_attack(cat):
    """Never zero: a lone player must still be under pressure."""
    s = _party_of(1)
    assert hazard_attack_count(s) == 1
    before = s["characters"]["q0"]["stamina"]
    hazard_attack(s, Dice(3))
    assert s["characters"]["q0"]["stamina"] < before


def test_attack_count_follows_living_characters_not_seats(cat):
    """Burn half the party out and the incoming volley shrinks with it --
    otherwise a losing position could never be recovered."""
    s = _party_of(8)
    assert hazard_attack_count(s) == 2
    s["characters"]["q0"]["stamina"] = 0
    s["characters"]["q1"]["stamina"] = 0
    s["characters"]["q2"]["stamina"] = 0
    s["characters"]["q3"]["stamina"] = 0
    assert hazard_attack_count(s) == 1


def test_a_multi_attack_round_hits_more_than_once(cat):
    s = _party_of(8)
    result = hazard_attack(s, Dice(3))
    assert result["count"] == 2 and len(result["attacks"]) == 2
    assert all(a["attack"] == "stress" for a in result["attacks"])


def test_multiple_stress_attacks_spread_across_the_party(cat):
    s = _party_of(8)
    result = hazard_attack(s, Dice(3))
    hit = {a["player_id"] for a in result["attacks"]}
    assert len(hit) == 2, "a round's attacks must not pile onto one character"


def test_a_stunned_hazard_skips_every_attack_of_the_round(cat):
    s = _party_of(8)
    assert hazard_attack_count(s) == 2
    add_condition(s, "stunned", "hazard", 1, 1)
    before = sum(c["stamina"] for c in s["characters"].values())
    result = hazard_attack(s, Dice(3))
    assert result["blocked"] == "stunned"
    assert sum(c["stamina"] for c in s["characters"].values()) == before


def test_a_shield_absorbs_across_attacks_until_it_is_spent(cat):
    s = _party_of(8)
    add_condition(s, "shield", "party", 3, 2)
    before = sum(c["stamina"] for c in s["characters"].values())
    result = hazard_attack(s, Dice(3))
    absorbed = sum(a["absorbed"] for a in result["attacks"])
    landed = sum(a["amount"] for a in result["attacks"])
    assert absorbed == 3, "the pool is shared across the whole round's attacks"
    assert not active_conditions(s, "shield"), "a spent shield is gone"
    assert sum(c["stamina"] for c in s["characters"].values()) == before - landed
    assert landed > 0, "a 3-point shield cannot soak a whole multi-attack round"


def test_resist_burn_blocks_every_burn_attack_in_the_round(cat):
    s = _party_of(8)
    s["hazards"][0]["attack_type"] = "burn_schedule"
    add_condition(s, "resist_burn", "party", 1, 2)
    result = hazard_attack(s, Dice(3))
    assert s["party"]["schedule"] == 100
    assert all(a["blocked"] == "resist_burn" for a in result["attacks"])


def test_no_debt_blocks_every_debt_attack_in_the_round(cat):
    s = _party_of(8)
    s["hazards"][0]["attack_type"] = "debt"
    add_condition(s, "no_debt", "hazard", 1, 2)
    result = hazard_attack(s, Dice(3))
    assert s["party"]["tech_debt"] == 0
    assert all(a["blocked"] == "no_debt" for a in result["attacks"])


def test_burn_attacks_repeat_their_own_type_rather_than_rolling_a_new_one(cat):
    s = _party_of(8)
    s["hazards"][0]["attack_type"] = "burn_budget"
    result = hazard_attack(s, Dice(3))
    assert [a["attack"] for a in result["attacks"]] == ["burn_budget"] * 2
    assert s["party"]["budget"] == 100 - sum(a["amount"] for a in result["attacks"])


def test_a_fumble_buys_one_extra_attack_not_a_whole_round(cat):
    """A fumble is one more attack. Scaling it too would punish a big party
    twice over for the same mistake."""
    s = _party_of(8)
    s["characters"]["q0"]["unlocked"].append("descope")
    s["turn"]["turn_index"] = 0
    result = resolve_action(s, "q0", cat.abilities["descope"], FixedDice([1]))
    attacks = [c for c in result.changes if c["kind"] == "hazard_attack"]
    assert len(attacks) == 1 and "attacks" not in attacks[0]
    assert attacks[0]["player_id"] == "q0"
