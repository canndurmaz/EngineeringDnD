"""The bot policy: one test per branch, each in the situation it is meant for.

Every state here is hand-built so the branch under test is the only one that
can fire -- a healthy party, a clean debt, and a revealed weakness are as much
part of a test for branch 5 as the abilities are.
"""
import pytest
from bots import choose_action, expected_damage
from engine.classes import load_catalog
from tests.engine.test_effects import make_state


@pytest.fixture(scope="module")
def catalog():
    return load_catalog("data")


def board(class_id, unlocked, *, focus=6, tech_debt=0, revealed=("weakness",),
          ally_stamina=12, used=None, conditions=()):
    """A two-seat table with the bot in seat one, and nothing else going on."""
    state = make_state()
    bot = state["characters"]["p1"]
    bot.update({"class_id": class_id, "unlocked": list(unlocked),
                "focus": focus, "max_focus": 8, "used": dict(used or {}),
                "is_bot": True, "stamina": 10, "max_stamina": 10})
    state["characters"]["p2"].update({"stamina": ally_stamina, "max_stamina": 12})
    state["party"]["tech_debt"] = tech_debt
    state["hazards"][0]["revealed"] = list(revealed)
    state["conditions"] = list(conditions)
    return state


# --- the five branches ------------------------------------------------------

def test_a_hurt_ally_is_healed_before_anything_else(catalog):
    """Half stamina is the line: below it, keeping someone standing wins."""
    state = board("mechatronics_engineer",
                  ["sensor_fusion", "actuator_integration"], ally_stamina=4)
    assert choose_action(state, "p1", catalog) == ("actuator_integration", "p2")


def test_the_heal_goes_to_the_most_hurt_ally(catalog):
    state = board("mechatronics_engineer",
                  ["sensor_fusion", "actuator_integration"], ally_stamina=5)
    state["characters"]["p1"]["stamina"] = 2      # the bot is worse off itself
    assert choose_action(state, "p1", catalog) == ("actuator_integration", "p1")


def test_debt_at_the_dc_threshold_is_paid_down(catalog):
    """At 10 the debt starts adding +1 to every DC in the party."""
    state = board("computer_scientist", ["refactor", "unit_test_barrage"],
                  tech_debt=10)
    assert choose_action(state, "p1", catalog) == ("refactor", None)


def test_debt_below_the_threshold_is_left_alone(catalog):
    state = board("computer_scientist", ["refactor", "unit_test_barrage"],
                  tech_debt=9)
    assert choose_action(state, "p1", catalog)[0] == "unit_test_barrage"


def test_an_unknown_weakness_is_scouted(catalog):
    """Knowing the weakness is +2 on every roll the party makes afterwards."""
    state = board("ee_engineer", ["signal_integrity_scan", "emi_shield"],
                  revealed=[])
    assert choose_action(state, "p1", catalog) == ("signal_integrity_scan", None)


def test_a_party_buff_nobody_is_running_is_switched_on(catalog):
    state = board("mechanical_technician", ["jig_and_fixture", "shop_floor_fix"])
    assert choose_action(state, "p1", catalog) == ("jig_and_fixture", None)


def test_a_party_buff_already_running_is_not_reapplied(catalog):
    """Once roll_bonus is up, re-casting it buys nothing -- go and hit things."""
    running = [{"name": "roll_bonus", "scope": "party", "value": 3,
                "rounds": 2, "target_id": None}]
    state = board("mechanical_technician", ["jig_and_fixture", "shop_floor_fix"],
                  conditions=running)
    assert choose_action(state, "p1", catalog) == ("shop_floor_fix", None)


def test_otherwise_it_attacks_with_the_best_expected_damage(catalog):
    """Half of a 30-severity hazard beats an average 2d4+RIGOR."""
    state = board("computer_scientist",
                  ["binary_search_debug", "unit_test_barrage"])
    assert choose_action(state, "p1", catalog) == ("binary_search_debug", None)


# --- legality ---------------------------------------------------------------

def test_an_unaffordable_ability_is_never_chosen(catalog):
    state = board("computer_scientist",
                  ["binary_search_debug", "unit_test_barrage"], focus=1)
    assert choose_action(state, "p1", catalog) == ("unit_test_barrage", None)


def test_a_locked_ability_is_never_chosen(catalog):
    """Refactor would clear the debt, but this bot has not unlocked it."""
    state = board("computer_scientist", ["unit_test_barrage"], tech_debt=40)
    assert choose_action(state, "p1", catalog) == ("unit_test_barrage", None)


def test_a_spent_once_per_ability_is_never_chosen(catalog):
    state = board("computer_scientist",
                  ["binary_search_debug", "unit_test_barrage"],
                  used={"binary_search_debug": "hazard:h1"})
    assert choose_action(state, "p1", catalog) == ("unit_test_barrage", None)


def test_nothing_affordable_means_passing(catalog):
    state = board("computer_scientist", ["binary_search_debug"], focus=0)
    assert choose_action(state, "p1", catalog) is None


def test_a_bot_that_is_not_seated_passes(catalog):
    state = board("computer_scientist", ["unit_test_barrage"])
    assert choose_action(state, "ghost", catalog) is None


def test_the_policy_mutates_nothing(catalog):
    """It is consulted mid-turn; a policy that edits the board is a bug."""
    import copy
    state = board("computer_scientist", ["unit_test_barrage"])
    before = copy.deepcopy(state)
    choose_action(state, "p1", catalog)
    assert state == before


# --- the damage estimate ----------------------------------------------------

def test_expected_damage_averages_a_dice_expression(catalog):
    state = board("computer_scientist", ["unit_test_barrage"])
    mods = {"RIGOR": 3}
    ability = catalog.abilities["unit_test_barrage"]     # 2d4+RIGOR
    assert expected_damage(state, ability, mods) == 2 * 2.5 + 3


def test_expected_damage_reads_a_fraction_of_the_hazard(catalog):
    state = board("computer_scientist", ["binary_search_debug"])
    ability = catalog.abilities["binary_search_debug"]   # half of severity 30
    assert expected_damage(state, ability, {}) == 15


def test_expected_damage_reads_the_party_focus_pool(catalog):
    state = board("ee_engineer", ["power_budget"], focus=4)
    pool = sum(c["focus"] for c in state["characters"].values())
    ability = catalog.abilities["power_budget"]          # 2 per point of focus
    assert expected_damage(state, ability, {}) == 2 * pool
