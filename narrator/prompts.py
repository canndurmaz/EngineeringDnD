# narrator/prompts.py
"""Prompt construction. The engine decided everything; the model only describes it."""
from __future__ import annotations

from narrator import settings

SYSTEM_PROMPT = (
    "You are the Game Master of an engineering RPG. Describe what just happened "
    "in 2-3 sentences of vivid, technically literate prose.\n"
    "RULES: Never invent numbers. Never contradict the stated outcome. "
    "Never decide what happens next. Do not address the player as \"you the user\". "
    "Do not mention dice, rolls, or difficulty classes. Write only the prose."
)

SAMPLING = {"temperature": 0.8, "max_tokens": settings.max_tokens(),
            "stop": ["\n\n"]}
GENESIS_SAMPLING = {"temperature": 0.9, "max_tokens": 220, "stop": ["\n\n\n"]}

_OUTCOME_LABEL = {"crit": "CRITICAL SUCCESS", "success": "SUCCESS",
                  "failure": "FAILURE", "fumble": "FUMBLE"}


def _turn_body(job: dict) -> str:
    hazard = job.get("hazard") or {}
    lines = [
        f"PROJECT: {job.get('premise') or 'an unnamed engineering programme'}",
        f"PHASE: {job.get('phase', 'Design')}",
    ]
    if hazard:
        lines.append(
            f"PROBLEM: {hazard.get('name')} "
            f"(severity {hazard.get('severity')}/{hazard.get('max_severity')})")
    lines += [
        f"ENGINEER: {job.get('actor_name')}, {job.get('actor_class')}",
        f"ACTION: {job.get('ability_name')}",
        f"OUTCOME: {_OUTCOME_LABEL.get(job.get('outcome'), 'SUCCESS')}",
    ]
    for change in job.get("changes", []):
        if change.get("kind") == "hazard_damage":
            lines.append(f"EFFECT: the problem was reduced by {change['amount']}")
        elif change.get("kind") == "party_delta":
            lines.append(
                f"EFFECT: {change['field'].replace('_', ' ')} changed by "
                f"{change['delta']}")
        elif change.get("kind") == "stress":
            lines.append(f"EFFECT: an engineer took {change['amount']} stress")
    return "\n".join(lines)


def build(job: dict) -> list:
    kind = job.get("kind", "turn")

    if kind == "genesis":
        body = (
            f"Invent a short engineering programme brief.\n"
            f"PROGRAMME NAME: {job.get('room_name')}\n"
            f"SYSTEM: {job.get('archetype_hint')}\n"
            "Write 2-3 sentences naming the customer and one distinctive, "
            "difficult constraint. Do not use bullet points."
        )
    elif kind == "hazards":
        existing = ", ".join(job.get("existing", []))
        body = (
            f"PROJECT: {job.get('premise')}\n"
            f"PHASE: {job.get('phase')}\n"
            f"List exactly {job.get('count')} engineering problems that could "
            f"derail this phase.\n"
            f"Format each on its own line as: Name - one sentence description\n"
            f"Do not number them. Avoid repeating: {existing or 'nothing'}"
        )
    elif kind == "phase":
        body = (
            f"PROJECT: {job.get('premise')}\n"
            f"The team has just entered the {job.get('phase')} phase.\n"
            "Write 2-3 sentences marking the transition."
        )
    else:
        body = _turn_body(job)

    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": body}]
