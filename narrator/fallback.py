"""Template narrator. The game is fully playable with only this."""
from __future__ import annotations

_TURN = {
    "crit": [
        "{actor} does not merely solve {hazard} — {actor} dismantles the reason it existed. "
        "{ability} lands cleanly, and the room goes quiet in the way rooms do when "
        "something has just gone extremely right.",
        "{ability}, applied by {actor} with the confidence of someone who has been "
        "waiting weeks for exactly this. {hazard} simply stops being a problem.",
    ],
    "success": [
        "{actor} works {ability} against {hazard}. It gives, grudgingly, and the "
        "{phase} board moves one square closer to green.",
        "{ability} does what it says on the process document. {hazard} recedes, and "
        "{actor} writes it up before anyone can call it luck.",
        "There is no drama to it. {actor} applies {ability}, {hazard} yields, and the "
        "{phase} review gets one fewer open item.",
    ],
    "failure": [
        "{actor} tries {ability}. {hazard} absorbs it without comment and stays exactly "
        "where it was.",
        "{ability} was the right instinct and the wrong afternoon. {hazard} is unmoved, "
        "and the {phase} schedule notices.",
        "Nothing breaks. Nothing improves either. {hazard} outlasts {actor}'s {ability} "
        "and the team moves on to the next idea.",
    ],
    "fumble": [
        "{ability} goes wrong in a way that will be discussed for years. {hazard} gets "
        "worse, and {actor} is the one holding the multimeter when it does.",
        "{actor} reaches for {ability} and finds the one failure mode nobody modelled. "
        "{hazard} responds immediately and unkindly.",
    ],
}

_PHASE = [
    "The {phase} gate closes behind you. Whatever was true last week is a baseline now.",
    "{phase} begins. The problems change shape; the budget does not.",
]

_GENESIS = [
    'Programme "{room_name}": {hint}. The customer is enthusiastic, the schedule is '
    "fixed, and the requirements are still being written.",
    '"{room_name}" is {hint}. It has a launch date, a logo, and not yet a thermal model.',
]


def _pick(options: list, seq: int) -> str:
    """Rotate through the phrasings by sequence number.

    Deliberately not random: Random(n).choice() on a short list returns the same
    element for small consecutive n, which would make the narrator repeat itself.
    """
    return options[seq % len(options)]


class TemplateNarrator:
    """Deterministic per event, varied across events. Never claims a die roll."""

    name = "template"

    def narrate(self, job: dict) -> str:
        # A phase interlude belongs to no single action, so its event_seq is
        # None; `or 0` keeps int() from raising on it.
        seq = int(job.get("event_seq") or 0)
        kind = job.get("kind", "turn")

        if kind == "genesis":
            return _pick(_GENESIS, seq).format(
                room_name=job.get("room_name", "the programme"),
                hint=job.get("archetype_hint", "a complex engineering system"))

        if kind == "phase":
            return _pick(_PHASE, seq).format(phase=job.get("phase", "the next phase"))

        hazard = job.get("hazard") or {}
        return _pick(_TURN[job.get("outcome", "success")], seq).format(
            actor=job.get("actor_name", "The engineer"),
            ability=job.get("ability_name", "the obvious approach"),
            hazard=hazard.get("name", "the problem"),
            phase=job.get("phase", "current"))
