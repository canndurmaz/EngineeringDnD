# narrator/genesis.py
"""Room genesis: the programme premise and a rewrite of every hazard's prose.

Runs at low priority while players are still joining, so the slowest generation
hides inside lobby time. It rewrites names and descriptions only — never mechanics.
"""
from __future__ import annotations

import re

from engine.phases import PHASES

# A dash needs space on BOTH sides so hyphenated names survive ("Tolerance Stack-Up");
# a colon needs space only after it, because "Name: description" is what models emit.
_SEPARATOR = re.compile(r"\s+[-–—]\s+|\s*:\s+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_MAX_NAME = 60


def parse_hazard_lines(text: str, expected: int) -> list:
    """Parse 'Name - description' lines. Line-based beats JSON for a 1B model."""
    out: list = []
    for raw in (text or "").splitlines():
        line = _BULLET.sub("", raw.strip())
        if not line:
            continue
        parts = _SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        name, description = parts[0].strip(), parts[1].strip()
        if not name or not description or len(name) > _MAX_NAME:
            continue
        out.append((name, description))
        if len(out) == expected:
            break
    return out


def genesis_jobs(room_id: str, state: dict, archetype: dict) -> list:
    """One premise job, then one hazard-rewrite job per phase."""
    jobs = [{
        "kind": "genesis", "priority": 2, "room_id": room_id, "event_seq": None,
        "room_name": state["room"]["name"],
        "archetype_hint": archetype.get("hint", "a complex engineering system"),
    }]
    for phase_index, (_, phase_name) in enumerate(PHASES):
        in_phase = [h for h in state["hazards"]
                    if h["phase_index"] == phase_index and not h["is_boss"]]
        if not in_phase:
            continue
        jobs.append({
            "kind": "hazards", "priority": 3, "room_id": room_id,
            "event_seq": None, "phase_index": phase_index, "phase": phase_name,
            "count": len(in_phase),
            "existing": [h["name"] for h in in_phase],
            "premise": state["room"].get("premise", ""),
        })
    return jobs
