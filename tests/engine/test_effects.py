import pytest
from engine.dice import Dice
from engine.effects import (EffectError, active_conditions, add_condition,
                            apply_effects, condition_total, expire_conditions)


def make_state():
    return {
        "room": {"id": "r", "phase_index": 0, "status": "active"},
        "party": {"budget": 100, "schedule": 100, "tech_debt": 0},
        "characters": {
            "p1": {"player_id": "p1", "name": "Ada", "class_id": "computer_scientist",
                   "stats": {"RIGOR": 16, "INTUITION": 14, "CRAFT": 10,
                             "SYSTEMS": 12, "COMMS": 10, "GRIT": 10},
                   "level": 1, "stamina": 10, "max_stamina": 10, "focus": 4,
                   "max_focus": 7,
                   "unlocked": ["refactor", "unit_test_barrage"], "used": {}},
            "p2": {"player_id": "p2", "name": "Ben", "class_id": "mechanical_technician",
                   "stats": {"RIGOR": 10, "INTUITION": 10, "CRAFT": 15,
                             "SYSTEMS": 10, "COMMS": 10, "GRIT": 14},
                   "level": 1, "stamina": 4, "max_stamina": 12, "focus": 2,
                   "max_focus": 4, "unlocked": ["shop_floor_fix"], "used": {}},
        },
        "hazards": [{"id": "h1", "phase_index": 0, "ordinal": 0,
                     "name": "Thermal Runaway",
                     "description": "The pack heats faster than it can shed.",
                     "severity": 30, "max_severity": 30, "dc": 12,
                     "attack_type": "stress", "weakness": "CRAFT",
                     "revealed": [], "defeated": False, "is_boss": False}],
        "active_hazard_id": "h1",
        "turn": {"round": 1, "order": ["p1", "p2"], "turn_index": 0},
        "conditions": [],
    }


def ctx(state, actor="p1", target=None, crit=False):
    mods = {k: (v - 10) // 2 for k, v in state["characters"][actor]["stats"].items()}
    return {"actor_id": actor, "target_id": target, "mods": mods, "crit": crit}


def test_damage_hazard_reduces_severity():
    s = make_state()
    apply_effects(s, [{"damage_hazard": "7"}], ctx(s), Dice(1))
    assert s["hazards"][0]["severity"] == 23


def test_damage_hazard_never_goes_below_zero():
    s = make_state()
    apply_effects(s, [{"damage_hazard": "999"}], ctx(s), Dice(1))
    assert s["hazards"][0]["severity"] == 0


def test_damage_hazard_evaluates_stat_expressions():
    s = make_state()
    changes = apply_effects(s, [{"damage_hazard": "1d1+RIGOR"}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 1 + 3  # RIGOR 16 -> +3


def test_crit_doubles_damage():
    s = make_state()
    changes = apply_effects(s, [{"damage_hazard": "7"}], ctx(s, crit=True), Dice(1))
    assert changes[0]["amount"] == 14


def test_crit_does_not_double_technical_debt():
    s = make_state()
    apply_effects(s, [{"party": {"tech_debt": 3}}], ctx(s, crit=True), Dice(1))
    assert s["party"]["tech_debt"] == 3


def test_crit_does_not_double_a_budget_penalty():
    s = make_state()
    apply_effects(s, [{"party": {"budget": -5}}], ctx(s, crit=True), Dice(1))
    assert s["party"]["budget"] == 95


def test_crit_doubles_a_budget_gain():
    s = make_state()
    apply_effects(s, [{"party": {"budget": 15}}], ctx(s, crit=True), Dice(1))
    assert s["party"]["budget"] == 130


def test_fraction_damage_halves_remaining_severity():
    s = make_state()
    s["hazards"][0]["severity"] = 21
    apply_effects(s, [{"damage_hazard": {"fraction": 0.5}}], ctx(s), Dice(1))
    assert s["hazards"][0]["severity"] == 11  # ceil-free: 21 - int(21*0.5)=21-10


def test_per_party_focus_damage_scales_with_unspent_focus():
    s = make_state()  # p1 focus 4, p2 focus 2 -> 6 total
    changes = apply_effects(s, [{"damage_hazard": {"per_party_focus": 2}}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 12


def test_double_if_weakness_applies_when_matched():
    s = make_state()  # hazard weakness is CRAFT
    changes = apply_effects(
        s, [{"damage_hazard": "5", "double_if_weakness": "CRAFT"}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 10


def test_double_if_weakness_is_inert_when_unmatched():
    s = make_state()
    changes = apply_effects(
        s, [{"damage_hazard": "5", "double_if_weakness": "COMMS"}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 5


def test_party_delta_applies_multiple_fields():
    s = make_state()
    apply_effects(s, [{"party": {"budget": 10, "tech_debt": 1}}], ctx(s), Dice(1))
    assert s["party"]["budget"] == 110 and s["party"]["tech_debt"] == 1


def test_tech_debt_floors_at_zero():
    s = make_state()
    apply_effects(s, [{"party": {"tech_debt": -5}}], ctx(s), Dice(1))
    assert s["party"]["tech_debt"] == 0


def test_heal_ally_restores_stamina_without_exceeding_max():
    s = make_state()
    apply_effects(s, [{"heal_ally": "50"}], ctx(s, target="p2"), Dice(1))
    assert s["characters"]["p2"]["stamina"] == 12


def test_heal_ally_defaults_to_the_actor_when_no_target():
    s = make_state()
    s["characters"]["p1"]["stamina"] = 5
    apply_effects(s, [{"heal_ally": "3"}], ctx(s), Dice(1))
    assert s["characters"]["p1"]["stamina"] == 8


def test_restore_focus_party_scope_tops_up_everyone():
    s = make_state()
    apply_effects(s, [{"restore_focus": {"scope": "party", "amount": "2"}}],
                  ctx(s), Dice(1))
    assert s["characters"]["p1"]["focus"] == 6
    assert s["characters"]["p2"]["focus"] == 4  # capped at max_focus


def test_stress_self_damages_the_actor():
    s = make_state()
    apply_effects(s, [{"stress_self": "3"}], ctx(s), Dice(1))
    assert s["characters"]["p1"]["stamina"] == 7


def test_stamina_floors_at_zero():
    s = make_state()
    apply_effects(s, [{"stress_self": "99"}], ctx(s), Dice(1))
    assert s["characters"]["p1"]["stamina"] == 0


def test_reveal_weakness_marks_the_hazard():
    s = make_state()
    apply_effects(s, [{"reveal": {"what": "weakness"}}], ctx(s), Dice(1))
    assert "weakness" in s["hazards"][0]["revealed"]


def test_reveal_all_marks_every_field():
    s = make_state()
    apply_effects(s, [{"reveal": {"what": "all"}}], ctx(s), Dice(1))
    assert set(s["hazards"][0]["revealed"]) >= {"weakness", "dc", "next_attack"}


def test_apply_condition_registers_a_party_condition():
    s = make_state()
    apply_effects(s, [{"apply_condition": {"scope": "party", "condition": "dc_delta",
                                           "value": -3, "rounds": 1}}], ctx(s), Dice(1))
    assert condition_total(s, "dc_delta", "p2") == -3


def test_conditions_stack_additively():
    s = make_state()
    add_condition(s, "roll_bonus", "party", 2, 3)
    add_condition(s, "roll_bonus", "party", 3, 3)
    assert condition_total(s, "roll_bonus", "p1") == 5


def test_ally_scoped_condition_does_not_reach_other_players():
    s = make_state()
    add_condition(s, "roll_bonus", "ally", 4, 1, target_id="p2")
    assert condition_total(s, "roll_bonus", "p2") == 4
    assert condition_total(s, "roll_bonus", "p1") == 0


def test_expire_conditions_decrements_and_drops():
    s = make_state()
    add_condition(s, "roll_bonus", "party", 2, 1)
    expire_conditions(s)
    assert active_conditions(s, "roll_bonus") == []


def test_shield_registers_absorbing_condition():
    s = make_state()
    apply_effects(s, [{"shield": {"scope": "party", "amount": "6", "rounds": 1}}],
                  ctx(s), Dice(1))
    assert condition_total(s, "shield", "p1") == 6


def test_reroll_grant_registers_for_the_target():
    s = make_state()
    apply_effects(s, [{"reroll_grant": {"scope": "ally", "count": 1}}],
                  ctx(s, target="p2"), Dice(1))
    assert condition_total(s, "reroll", "p2") == 1


def test_skip_hazard_defeats_it_and_queues_a_larger_return():
    s = make_state()
    apply_effects(s, [{"skip_hazard": {"return_multiplier": 1.5}}], ctx(s), Dice(1))
    assert s["hazards"][0]["defeated"] is True
    returned = [h for h in s["hazards"] if h["id"] != "h1"]
    assert len(returned) == 1
    assert returned[0]["max_severity"] == 45
    assert returned[0]["phase_index"] == 1


def test_copy_ability_grants_a_borrow_condition():
    s = make_state()
    apply_effects(s, [{"copy_ability": {"scope": "ally"}}], ctx(s), Dice(1))
    assert condition_total(s, "borrowed_ability", "p1") == 1


def test_unknown_verb_raises():
    s = make_state()
    with pytest.raises(EffectError, match="unknown"):
        apply_effects(s, [{"summon_dragon": "1"}], ctx(s), Dice(1))


def test_effect_with_two_verbs_raises():
    s = make_state()
    with pytest.raises(EffectError, match="exactly one"):
        apply_effects(s, [{"damage_hazard": "1", "heal_ally": "1"}], ctx(s), Dice(1))


def test_bonus_if_repeated_does_not_fire_without_a_prior_ability():
    s = make_state()
    changes = apply_effects(
        s, [{"damage_hazard": "10", "bonus_if_repeated": 0.5}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 10


class _StubAbility:
    def __init__(self, ability_id):
        self.id = ability_id


def test_bonus_if_repeated_fires_when_ability_matches_last_used():
    s = make_state()
    s["last_ability_id"] = "pid_tune"
    c = ctx(s)
    c["ability"] = _StubAbility("pid_tune")
    changes = apply_effects(
        s, [{"damage_hazard": "10", "bonus_if_repeated": 0.5}], c, Dice(1))
    assert changes[0]["amount"] == 15


def test_crit_does_not_double_stress_self():
    s = make_state()
    apply_effects(s, [{"stress_self": "3"}], ctx(s, crit=True), Dice(1))
    assert s["characters"]["p1"]["stamina"] == 7


def test_crit_doubles_fraction_damage():
    s = make_state()
    s["hazards"][0]["severity"] = 20
    changes = apply_effects(
        s, [{"damage_hazard": {"fraction": 0.5}}], ctx(s, crit=True), Dice(1))
    assert changes[0]["amount"] == 20


def test_crit_doubles_per_party_focus_damage():
    s = make_state()  # p1 focus 4, p2 focus 2 -> 6 total * 2 = 12, crit doubles to 24
    changes = apply_effects(
        s, [{"damage_hazard": {"per_party_focus": 2}}], ctx(s, crit=True), Dice(1))
    assert changes[0]["amount"] == 24
