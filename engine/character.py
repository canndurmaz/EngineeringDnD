"""Character creation and level up."""
from __future__ import annotations

from engine.classes import STATS, Catalog, CharacterClass
from engine.dice import Dice
from engine.rules import stat_mod


class CharacterError(Exception):
    """Raised on an invalid character operation."""


def _four_d6_drop_lowest(dice: Dice) -> dict:
    """One 4d6-drop-lowest roll, kept as a showable record of what happened."""
    rolls = [dice.randint(1, 6) for _ in range(4)]
    dropped = min(rolls)
    return {"dice": rolls, "dropped": dropped, "total": sum(rolls) - dropped}


def roll_stats_detailed(cls: CharacterClass, dice: Dice) -> tuple[dict, dict]:
    """Roll stats and return them alongside the dice that produced them.

    The detail is purely for the one-time reveal on the character-select screen:
    six rolls in the order they were rolled, each carrying its four raw d6, the
    die that was dropped, the total of the three kept, and the stat it landed in.
    """
    rolls = [_four_d6_drop_lowest(dice) for _ in range(6)]
    # Best two totals seat in the class stats; the rest land in a shuffled order.
    ordered = sorted(rolls, key=lambda r: r["total"], reverse=True)
    rest = [s for s in STATS if s not in (cls.primary, cls.secondary)]
    dice.shuffle(rest)
    stats = {}
    for roll, name in zip(ordered, [cls.primary, cls.secondary] + rest):
        roll["stat"] = name
        stats[name] = max(8, min(16, roll["total"]))
    detail = {"rolls": rolls, "primary": cls.primary, "secondary": cls.secondary}
    return {s: stats[s] for s in STATS}, detail


def roll_stats(cls: CharacterClass, dice: Dice) -> dict:
    """Roll 4d6-drop-lowest six times, seat the best two in the class stats."""
    return roll_stats_detailed(cls, dice)[0]


def max_stamina(stats: dict, level: int) -> int:
    return 8 + stat_mod(stats["GRIT"]) + 2 * level


def max_focus(stats: dict) -> int:
    return 4 + max(stat_mod(stats["RIGOR"]), stat_mod(stats["SYSTEMS"]))


def new_character_detailed(player_id: str, name: str, cls: CharacterClass,
                           catalog: Catalog, dice: Dice) -> tuple[dict, dict]:
    """A fresh character plus the roll breakdown that made it."""
    stats, detail = roll_stats_detailed(cls, dice)
    stamina, focus = max_stamina(stats, 1), max_focus(stats)
    char = {
        "player_id": player_id,
        "name": name,
        "class_id": cls.id,
        "stats": stats,
        "level": 1,
        "stamina": stamina,
        "max_stamina": stamina,
        "focus": focus,
        "max_focus": focus,
        "unlocked": [a.id for a in catalog.unlocked_for(cls.id, 0)],
        "used": {},
    }
    return char, detail


def new_character(player_id: str, name: str, cls: CharacterClass,
                  catalog: Catalog, dice: Dice) -> dict:
    return new_character_detailed(player_id, name, cls, catalog, dice)[0]


def level_up(char: dict, catalog: Catalog, stat_choice: str,
             phase_index: int) -> dict:
    if stat_choice not in STATS:
        raise CharacterError(f"unknown stat {stat_choice!r}")
    char["stats"][stat_choice] += 1
    char["level"] += 1
    char["max_stamina"] = max_stamina(char["stats"], char["level"])
    char["stamina"] = min(char["max_stamina"], max(1, char["stamina"]) + 2)
    char["max_focus"] = max_focus(char["stats"])
    char["focus"] = char["max_focus"]
    char["used"] = {}

    available = {a.id for a in catalog.unlocked_for(char["class_id"], phase_index)}
    new_abilities = sorted(available - set(char["unlocked"]))
    char["unlocked"].extend(new_abilities)
    return {
        "level": char["level"],
        "stat_raised": stat_choice,
        "max_stamina": char["max_stamina"],
        "new_abilities": new_abilities,
    }
