import pytest
from engine.classes import STATS, load_catalog

EXPECTED_ABILITY_IDS = {
    "mechanical_engineer": {"fea_deep_dive", "design_margin", "tolerance_stack_up", "thermal_sink"},
    "computer_scientist": {"binary_search_debug", "unit_test_barrage", "refactor", "rubber_duck"},
    "ee_engineer": {"signal_integrity_scan", "emi_shield", "power_budget", "board_respin"},
    "system_engineer": {"requirements_trace", "trade_study", "icd_lockdown", "vv_sweep"},
    "product_manager": {"descope", "stakeholder_charm", "roadmap_rally", "reprioritize"},
    "mechanical_technician": {"shop_floor_fix", "jig_and_fixture", "torque_to_spec", "scavenge_parts"},
    "electrical_technician": {"continuity_check", "solder_bodge", "harness_rework", "instrumentation_setup"},
    "control_systems_engineer": {"kalman_filter", "pid_tune", "stability_margin", "model_in_the_loop"},
    "mechatronics_engineer": {"sensor_fusion", "actuator_integration", "rapid_prototype", "cross_domain_hack"},
}


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_exactly_thirty_six_abilities(cat):
    assert len(cat.abilities) == 36


def test_every_class_has_its_four_named_abilities(cat):
    for class_id, expected in EXPECTED_ABILITY_IDS.items():
        got = {a.id for a in cat.abilities_for(class_id)}
        assert got == expected, f"{class_id}: expected {expected}, got {got}"


def test_every_class_has_exactly_two_starting_abilities(cat):
    for class_id in EXPECTED_ABILITY_IDS:
        starters = [a for a in cat.abilities_for(class_id) if a.unlock_phase == 0]
        assert len(starters) == 2, f"{class_id} has {len(starters)} starters"


def test_focus_costs_are_in_the_zero_to_three_band(cat):
    for a in cat.abilities.values():
        assert 0 <= a.focus_cost <= 3, f"{a.id} costs {a.focus_cost}"


def test_every_ability_has_at_least_one_success_effect_or_a_special_rule(cat):
    for a in cat.abilities.values():
        special = a.fixed_roll is not None or a.no_crit or a.no_fumble
        assert a.on_success or special, f"{a.id} does nothing on success"


def test_every_ability_has_nonempty_flavor(cat):
    for a in cat.abilities.values():
        assert a.flavor.strip(), f"{a.id} has no flavor text"


def test_kalman_filter_is_a_flat_eleven_with_no_swing(cat):
    k = cat.abilities["kalman_filter"]
    assert k.fixed_roll == 11 and k.no_crit and k.no_fumble


def test_unit_test_barrage_cannot_fumble(cat):
    assert cat.abilities["unit_test_barrage"].no_fumble


def test_board_respin_costs_budget(cat):
    assert cat.abilities["board_respin"].extra_cost.get("budget") == 15


def test_debt_adding_abilities_actually_add_debt(cat):
    for ability_id in ("descope", "solder_bodge", "scavenge_parts", "rapid_prototype"):
        effects = cat.abilities[ability_id].on_success
        assert any("party" in e and e["party"].get("tech_debt", 0) > 0 for e in effects), \
            f"{ability_id} should add Technical Debt"


def test_binary_search_debug_is_once_per_hazard(cat):
    assert cat.abilities["binary_search_debug"].once_per == "hazard"


def test_cross_domain_hack_is_once_per_phase(cat):
    assert cat.abilities["cross_domain_hack"].once_per == "phase"
