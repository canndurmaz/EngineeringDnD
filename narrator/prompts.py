# narrator/prompts.py
"""Prompt construction. The engine decided everything; the model only describes it."""
from __future__ import annotations

from narrator import settings

SYSTEM_PROMPT = (
    "You are the Game Master of an engineering RPG. Describe what just happened "
    "in 2-3 sentences of vivid, technically literate prose.\n"
    "CONTINUITY: You are writing the next passage of one continuous story, not a "
    "fresh scene. If STORY SO FAR is given, carry that thread forward -- do not "
    "restate, summarise or repeat what it already said. Never open a passage "
    "with the engineer's name; vary how each passage begins.\n"
    "RULES: Never invent numbers. Never contradict the stated outcome. "
    "Never decide what happens next. Do not address the player as \"you the user\". "
    "Do not mention dice, rolls, or difficulty classes. Write only the prose."
)

#: Each remembered passage is clipped to roughly this many characters. Two of
#: them is about 100 tokens, which the 1024-token context absorbs without
#: crowding out the facts the model must not contradict.
HISTORY_CHARS = 200
#: At most this many passages ride along, so the prompt cannot grow with the
#: length of the game.
HISTORY_LIMIT = 2

SAMPLING = {"temperature": 0.8, "max_tokens": settings.max_tokens(),
            "stop": ["\n\n"]}
GENESIS_SAMPLING = {"temperature": 0.9, "max_tokens": 220, "stop": ["\n\n\n"]}

_OUTCOME_LABEL = {"crit": "CRITICAL SUCCESS", "success": "SUCCESS",
                  "failure": "FAILURE", "fumble": "FUMBLE"}


def _clip(text: str, limit: int = HISTORY_CHARS) -> str:
    """The first `limit` characters, cut at a word boundary where there is one."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,;:-") + "..."


def _history_lines(job: dict) -> list:
    """The STORY SO FAR block, or nothing at all on a room's first turn.

    Omitted entirely rather than written as an empty heading: a heading with no
    content under it is exactly the kind of thing a small model fills in.
    """
    passages = [p for p in (job.get("history") or []) if str(p).strip()]
    passages = passages[-HISTORY_LIMIT:]
    if not passages:
        return []
    lines = ["STORY SO FAR (the passages just before this one; continue this "
             "thread, do not repeat it):"]
    lines += [f"- {_clip(p)}" for p in passages]
    return lines


#: The remaining severity, as a fraction, spelled out. Written in words rather
#: than digits on purpose: narrator.filters drops any sentence quoting a number
#: the engine did not commit, and a model that echoes "60%" back would lose the
#: whole passage to the template. The fraction still reaches the model; only its
#: spelling changes.
_FRACTIONS = [
    (0.95, "barely touched -- nearly all of the original severity is still there"),
    (0.75, "down to about three quarters of the severity it started with"),
    (0.55, "down to roughly two thirds of the severity it started with"),
    (0.45, "about half worn down"),
    (0.25, "down to about a third of the severity it started with"),
    (0.05, "down to a fraction of what it was -- nearly closed out"),
    (0.0, "finished, with nothing left of it"),
]


def _fraction_phrase(remaining: float) -> str:
    for floor, phrase in _FRACTIONS:
        if remaining >= floor:
            return phrase
    return _FRACTIONS[-1][1]


def _continuity_line(job: dict) -> str:
    """What has changed since those passages. A 1B model will not infer any of
    it, so it is stated outright: the phase, how far the problem has come, and
    whether the same pair of hands is still on it."""
    parts = [f"the {job.get('phase', 'Design')} phase is still running"]
    remaining = job.get("severity_remaining")
    if isinstance(remaining, (int, float)):
        parts.append("the problem is " + _fraction_phrase(float(remaining)))
    parts.append("the same engineer is acting again" if job.get("same_engineer")
                 else "a different engineer has the floor now")
    return "SINCE THEN: " + "; ".join(parts) + "."


def _turn_body(job: dict) -> str:
    hazard = job.get("hazard") or {}
    lines = _history_lines(job)
    if lines:
        lines.append(_continuity_line(job))
        lines.append("")
    lines += [
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
