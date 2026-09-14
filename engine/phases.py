# engine/phases.py
"""Phase progression, campaign construction, and win/lose conditions."""
from __future__ import annotations

import json
from pathlib import Path

from engine.character import level_up
from engine.classes import Catalog
from engine.dice import Dice

PHASES: tuple = (
    ("requirements", "Requirements"),
    ("design", "Design"),
    ("prototype", "Prototype"),
    ("integration", "Integration"),
    ("qualification", "Qualification"),
)

PHASE_BUDGET_BONUS = 10
PHASE_SCHEDULE_BONUS = 10

_NORMAL_SEVERITY_BASE, _NORMAL_SEVERITY_STEP = 20, 8
_BOSS_SEVERITY_BASE, _BOSS_SEVERITY_STEP = 35, 10
_NORMAL_DC_BASE, _BOSS_DC_BASE = 11, 13


def load_hazard_templates(data_dir: str = "data") -> dict:
    return json.loads((Path(data_dir) / "hazard_templates.json").read_text())


def load_archetypes(data_dir: str = "data") -> list:
    return json.loads((Path(data_dir) / "archetypes.json").read_text())


def _hazard_from_template(template: dict, phase_index: int, ordinal: int,
                          is_boss: bool, subsystem: str = "") -> dict:
    if is_boss:
        max_severity = _BOSS_SEVERITY_BASE + _BOSS_SEVERITY_STEP * phase_index
        dc = _BOSS_DC_BASE + phase_index
    else:
        max_severity = _NORMAL_SEVERITY_BASE + _NORMAL_SEVERITY_STEP * phase_index
        dc = _NORMAL_DC_BASE + phase_index
    hazard = {
        "id": f"h{phase_index}_{ordinal}",
        "phase_index": phase_index,
        "ordinal": ordinal,
        "name": template["name"],
        "description": template["description"],
        "severity": max_severity,
        "max_severity": max_severity,
        "dc": dc,
        "attack_type": template["attack_type"],
        "weakness": template["weakness"],
        "revealed": [],
        "defeated": False,
        "is_boss": is_boss,
        # Where in the machine the problem lives. "" means the campaign was
        # built without an archetype's subsystem list -- the map then simply
        # has nothing to light up, which is what an old room looks like.
        "subsystem": subsystem,
    }
    # Spec 2.6: a gate boss carries one special rule. It is template data, kept
    # off the hazard entirely when there is none -- same rule as `subsystem`, so
    # an ordinary problem's state has exactly the shape it always had.
    if template.get("rule"):
        hazard["rule"] = template["rule"]
    return hazard


def _deal_subsystems(dice: Dice, subsystem_ids: list, count: int) -> list:
    """`count` subsystem ids, dealt from a shuffled deck so one phase spreads
    across the machine instead of piling into whichever node sorts first.

    The shuffle is the injected Dice, so a seed still reproduces the placement
    exactly; the deck is re-cut per phase so a long phase wraps round rather
    than running out.
    """
    if not subsystem_ids:
        return [""] * count
    deck = list(subsystem_ids)
    dice.shuffle(deck)
    return [deck[index % len(deck)] for index in range(count)]


def build_campaign(dice: Dice, templates: dict,
                   subsystems: "list | None" = None) -> list:
    """Build every hazard for all five phases. Boss is always last in its phase.

    `subsystems` is the archetype's subsystem list (dicts with an "id"), and
    every hazard is placed in one of them. Omitting it is legal and leaves the
    placement blank -- the ruleset does not depend on it.
    """
    subsystem_ids = [s["id"] for s in (subsystems or [])]
    hazards: list = []
    for phase_index, (phase_id, _) in enumerate(PHASES):
        pool = list(templates[phase_id]["normal"])
        dice.shuffle(pool)
        count = dice.randint(2, 3)
        # count normals plus the boss, all placed in one deal so the whole
        # phase is spread over the machine.
        placed = _deal_subsystems(dice, subsystem_ids, count + 1)
        for ordinal, template in enumerate(pool[:count]):
            hazards.append(_hazard_from_template(template, phase_index, ordinal,
                                                 False, placed[ordinal]))
        hazards.append(_hazard_from_template(
            templates[phase_id]["boss"], phase_index, count, True, placed[count]))
    return hazards


def next_hazard_id(state) -> "str | None":
    phase_index = state["room"]["phase_index"]
    for hazard in state["hazards"]:
        if hazard["phase_index"] == phase_index and not hazard["defeated"]:
            return hazard["id"]
    return None


def phase_cleared(state) -> bool:
    return next_hazard_id(state) is None


def check_end_conditions(state) -> "str | None":
    if state["room"].get("status") == "won":
        return "win"
    if state["party"]["budget"] <= 0:
        return "lose_budget"
    if state["party"]["schedule"] <= 0:
        return "lose_schedule"
    if state["characters"] and all(
            c["stamina"] <= 0 for c in state["characters"].values()):
        return "lose_burnout"
    return None


def advance_phase(state, catalog: Catalog, stat_choices: dict) -> dict:
    """Clear the phase: level everyone, replenish, revive, and move on."""
    current = state["room"]["phase_index"]
    if current >= len(PHASES) - 1:
        state["room"]["status"] = "won"
        return {"phase": PHASES[current][0], "won": True, "levelled": {}}

    new_index = current + 1
    state["room"]["phase_index"] = new_index
    state["party"]["budget"] += PHASE_BUDGET_BONUS
    state["party"]["schedule"] += PHASE_SCHEDULE_BONUS
    state["conditions"] = []

    levelled = {}
    for player_id, char in state["characters"].items():
        if char["stamina"] <= 0:
            char["stamina"] = 1          # revived, but only just
        levelled[player_id] = level_up(
            char, catalog, stat_choices.get(player_id, "GRIT"), new_index)
        # Clearing a gate buys the party a breather, not a fresh start: stamina
        # comes back up to half of maximum and no further. Without it a campaign
        # is arithmetic -- five phases of damage against one pool that never
        # refills -- and with a full heal the damage stops mattering at all.
        char["stamina"] = max(char["stamina"], char["max_stamina"] // 2)

    state["active_hazard_id"] = next_hazard_id(state)
    state["turn"]["round"] = 1
    state["turn"]["turn_index"] = 0
    return {"phase": PHASES[new_index][0], "won": False, "levelled": levelled}
