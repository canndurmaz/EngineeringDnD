import pytest
from engine.character import (CharacterError, level_up, max_focus, max_stamina,
                              new_character, roll_stats, roll_stats_detailed)
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


# --- the 4d6 breakdown shown on the character-select screen ------------------
def test_roll_stats_detailed_reports_six_honest_rolls(cat):
    cls = cat.classes["mechanical_engineer"]
    for seed in range(20):
        _, detail = roll_stats_detailed(cls, Dice(seed))
        assert len(detail["rolls"]) == 6
        for roll in detail["rolls"]:
            assert len(roll["dice"]) == 4
            assert all(1 <= d <= 6 for d in roll["dice"])
            assert roll["dropped"] == min(roll["dice"])
            kept = list(roll["dice"])
            kept.remove(roll["dropped"])
            assert roll["total"] == sum(kept)


def test_detailed_totals_clamped_match_the_assigned_stats(cat):
    cls = cat.classes["computer_scientist"]
    for seed in range(20):
        stats, detail = roll_stats_detailed(cls, Dice(seed))
        assert {r["stat"] for r in detail["rolls"]} == set(STATS)
        for roll in detail["rolls"]:
            assert stats[roll["stat"]] == max(8, min(16, roll["total"]))


def test_primary_and_secondary_take_the_two_best_rolls(cat):
    cls = cat.classes["product_manager"]
    for seed in range(20):
        _, detail = roll_stats_detailed(cls, Dice(seed))
        assert detail["primary"] == cls.primary
        assert detail["secondary"] == cls.secondary
        by_stat = {r["stat"]: r["total"] for r in detail["rolls"]}
        totals = sorted(by_stat.values(), reverse=True)
        assert by_stat[cls.primary] == totals[0]
        assert by_stat[cls.secondary] == totals[1]


def test_roll_stats_still_matches_the_detailed_roll(cat):
    """roll_stats is unchanged: same seed, same stats, same determinism."""
    cls = cat.classes["system_engineer"]
    for seed in range(20):
        assert roll_stats(cls, Dice(seed)) == roll_stats(cls, Dice(seed))
        assert roll_stats(cls, Dice(seed)) == roll_stats_detailed(cls, Dice(seed))[0]


def test_detail_value_is_the_clamped_score_and_matches_the_stats(cat):
    """The reveal is pinned to the truth: `value` is what the character has."""
    cls = cat.classes["ee_engineer"]
    saw_a_clamp = False
    for seed in range(120):
        stats, detail = roll_stats_detailed(cls, Dice(seed))
        for roll in detail["rolls"]:
            assert roll["value"] == stats[roll["stat"]]
            assert roll["value"] == max(8, min(16, roll["total"]))
            if roll["total"] < 8 or roll["total"] > 16:
                saw_a_clamp = True
                assert roll["value"] != roll["total"]
            else:
                assert roll["value"] == roll["total"]
    assert saw_a_clamp, "no out-of-band total in 120 seeds; test proves nothing"
