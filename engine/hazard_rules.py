"""Gate-boss special rules: the one thing that makes a boss a boss.

Spec 2.6 asks for "one special rule" per gate boss. A rule is data -- it rides
on the hazard as {"id", "text", "params"} straight out of
data/hazard_templates.json -- and everything here is a pure reading of that
data. The rule ids are closed: an unknown id is inert rather than an error, so a
room written by a newer build never crashes an older one.

This module sits below both engine.rules and engine.effects because a rule
touches both halves of a turn: the roll (a stat penalty, an escalating DC) and
the damage that lands (a floor on chip hits). engine.rules already imports
engine.effects, so a shared leaf module is the only way for both to enforce the
same rule without an import cycle.
"""
from __future__ import annotations

#: Every rule id this build understands. Anything else is ignored.
RULE_IDS = frozenset({"no_descope", "rigor_only", "hands_on", "escalating",
                      "focused_fire"})


def rule_of(hazard) -> dict:
    """The active hazard's rule, or {} for a hazard that carries none."""
    if not hazard:
        return {}
    rule = hazard.get("rule") or {}
    return rule if rule.get("id") in RULE_IDS else {}


def _params(hazard) -> dict:
    return rule_of(hazard).get("params") or {}


def penalised_stats(hazard) -> tuple:
    """The stats this hazard punishes, as a tuple (empty when it punishes none)."""
    rule = rule_of(hazard)
    if rule.get("id") not in ("rigor_only", "hands_on"):
        return ()
    return tuple(_params(hazard).get("stats") or ())


def roll_penalty(hazard, stat_used: str) -> int:
    """How much this hazard takes off a roll made with `stat_used`.

    Returned as a negative number so a caller adds it to the roll bonus, which
    is what the player sees on the action line: a -4 that is visibly on the
    roll, not a DC that silently moved.
    """
    if stat_used in penalised_stats(hazard):
        return -int(_params(hazard).get("penalty", 4))
    return 0


def neutralises(hazard, ability) -> bool:
    """True when this hazard cancels the ability outright.

    `no_descope` reads the ability's *success* effects: an ability that buys
    progress with Technical Debt is the shortcut this boss exists to punish, and
    it is cancelled whether the roll lands or not. Costs are still paid, so the
    turn is spent -- that is the punishment.
    """
    if rule_of(hazard).get("id") != "no_descope":
        return False
    for effect in ability.on_success:
        delta = effect.get("party", {}).get("tech_debt")
        if isinstance(delta, (int, float)) and delta > 0:
            return True
    return False


def damage_floor(hazard, amount: int) -> int:
    """`focused_fire`: a hit under the threshold is reduced to the floor.

    A hit of exactly zero stays zero -- the rule blunts chip damage, it does not
    invent damage out of a miss.
    """
    rule = rule_of(hazard)
    if rule.get("id") != "focused_fire" or amount <= 0:
        return amount
    params = _params(hazard)
    if amount < int(params.get("threshold", 8)):
        return int(params.get("floor", 1))
    return amount


def escalate(hazard) -> int:
    """`escalating`: raise this hazard's DC for surviving another round.

    Returns the amount added (0 for any other rule). The counter is the
    hazard's own `dc`, so the next hazard starts from its own base and nothing
    needs resetting.
    """
    rule = rule_of(hazard)
    if rule.get("id") != "escalating" or hazard.get("defeated"):
        return 0
    step = int(_params(hazard).get("per_round", 1))
    hazard["dc"] += step
    return step
