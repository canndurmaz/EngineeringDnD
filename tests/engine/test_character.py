import pytest
from engine.character import (CharacterError, level_up, max_focus, max_stamina,
                              new_character, roll_stats)
from engine.classes import STATS, load_catalog
from engine.dice import Dice


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_roll_stats_produces_all_six(cat):
    stats = roll_stats(cat.classes["mechanical_engineer"], Dice(5))
    assert set(stats) == set(STATS)


def test_rolled_stats_stay_in_the_eight_to_sixteen_band(cat):
    for seed in range(50):
        stats = roll_stats(cat.classes["computer_scientist"], Dice(seed))
        assert all(8 <= v <= 16 for v in stats.values()), stats


def test_primary_stat_is_the_highest_rolled(cat):
    cls = cat.classes["product_manager"]     # COMMS / SYSTEMS
    for seed in range(30):
        stats = roll_stats(cls, Dice(seed))
        assert stats[cls.primary] == max(stats.values())


def test_secondary_is_at_least_as_high_as_the_other_four(cat):
    cls = cat.classes["product_manager"]
    for seed in range(30):
        stats = roll_stats(cls, Dice(seed))
        others = [v for k, v in stats.items()
                  if k not in (cls.primary, cls.secondary)]
        assert stats[cls.secondary] >= max(others)


def test_roll_stats_is_deterministic_for_a_seed(cat):
    cls = cat.classes["system_engineer"]
    assert roll_stats(cls, Dice(77)) == roll_stats(cls, Dice(77))


def test_max_stamina_follows_the_spec_formula():
    stats = {"RIGOR": 10, "INTUITION": 10, "CRAFT": 10,
             "SYSTEMS": 10, "COMMS": 10, "GRIT": 14}
    assert max_stamina(stats, 1) == 8 + 2 + 2


def test_max_focus_takes_the_better_of_rigor_and_systems():
    stats = {"RIGOR": 16, "INTUITION": 10, "CRAFT": 10,
             "SYSTEMS": 12, "COMMS": 10, "GRIT": 10}
    assert max_focus(stats) == 4 + 3


def test_new_character_starts_at_full_stamina_and_focus(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    assert char["stamina"] == char["max_stamina"]
    assert char["focus"] == char["max_focus"]
    assert char["level"] == 1


def test_new_character_knows_exactly_two_abilities(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    assert len(char["unlocked"]) == 2


def test_new_character_records_identity(cat):
    char = new_character("p7", "Ada", cat.classes["ee_engineer"], cat, Dice(9))
    assert char["player_id"] == "p7" and char["name"] == "Ada"
    assert char["class_id"] == "ee_engineer" and char["used"] == {}


def test_level_up_raises_the_chosen_stat(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    before = char["stats"]["GRIT"]
    level_up(char, cat, "GRIT", 1)
    assert char["stats"]["GRIT"] == before + 1


def test_level_up_increases_level_and_max_stamina(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    before = char["max_stamina"]
    level_up(char, cat, "RIGOR", 1)
    assert char["level"] == 2 and char["max_stamina"] == before + 2


def test_level_up_unlocks_newly_available_abilities(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    summary = level_up(char, cat, "RIGOR", 2)
    assert "refactor" in summary["new_abilities"]
    assert "refactor" in char["unlocked"]


def test_level_up_restores_focus_to_full(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    char["focus"] = 0
    level_up(char, cat, "RIGOR", 1)
    assert char["focus"] == char["max_focus"]


def test_level_up_clears_once_per_phase_usage(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    char["used"]["binary_search_debug"] = "phase:0"
    level_up(char, cat, "RIGOR", 1)
    assert char["used"] == {}


def test_level_up_rejects_an_unknown_stat(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    with pytest.raises(CharacterError, match="MAGIC"):
        level_up(char, cat, "MAGIC", 1)
