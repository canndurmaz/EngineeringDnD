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
                          is_boss: bool) -> dict:
    if is_boss:
        max_severity = _BOSS_SEVERITY_BASE + _BOSS_SEVERITY_STEP * phase_index
        dc = _BOSS_DC_BASE + phase_index
    else:
        max_severity = _NORMAL_SEVERITY_BASE + _NORMAL_SEVERITY_STEP * phase_index
        dc = _NORMAL_DC_BASE + phase_index
    return {
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
    }


def build_campaign(dice: Dice, templates: dict) -> list:
    """Build every hazard for all five phases. Boss is always last in its phase."""
    hazards: list = []
    for phase_index, (phase_id, _) in enumerate(PHASES):
        pool = list(templates[phase_id]["normal"])
        dice.shuffle(pool)
        count = dice.randint(2, 3)
        for ordinal, template in enumerate(pool[:count]):
            hazards.append(_hazard_from_template(template, phase_index, ordinal, False))
        hazards.append(_hazard_from_template(
            templates[phase_id]["boss"], phase_index, count, True))
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

    state["active_hazard_id"] = next_hazard_id(state)
    state["turn"]["round"] = 1
    state["turn"]["turn_index"] = 0
    return {"phase": PHASES[new_index][0], "won": False, "levelled": levelled}
