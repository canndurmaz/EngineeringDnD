"""Interpreter for the declarative effect verbs used in data/abilities.json."""
from __future__ import annotations

import math
from typing import Sequence

from engine.dice import Dice
from engine.hazard_rules import damage_floor

VERBS = frozenset({
    "damage_hazard", "heal_ally", "restore_focus", "party", "stress_self",
    "apply_condition", "reveal", "reroll_grant", "shield", "skip_hazard",
    "copy_ability",
})
MODIFIERS = frozenset({"double_if_weakness", "bonus_if_repeated"})

# Crit doubles beneficial numbers only. Penalties are never amplified by good luck.
_PENALTY_FIELDS = {"tech_debt"}


class EffectError(Exception):
    """Raised when an effect dict is malformed or names an unknown verb."""


# --- conditions -------------------------------------------------------------

def add_condition(state, name, scope, value, rounds, target_id=None) -> None:
    state.setdefault("conditions", []).append({
        "name": name, "scope": scope, "value": value,
        "rounds": rounds, "target_id": target_id,
    })


def active_conditions(state, name, player_id=None) -> list:
    out = []
    for c in state.get("conditions", []):
        if c["name"] != name:
            continue
        if c["scope"] == "party" or player_id is None or c["target_id"] == player_id:
            out.append(c)
    return out


def condition_total(state, name, player_id=None) -> int:
    return sum(c["value"] for c in active_conditions(state, name, player_id))


def expire_conditions(state) -> None:
    remaining = []
    for c in state.get("conditions", []):
        c["rounds"] -= 1
        if c["rounds"] > 0:
            remaining.append(c)
    state["conditions"] = remaining


# --- helpers ----------------------------------------------------------------

def _hazard(state):
    hid = state.get("active_hazard_id")
    for h in state["hazards"]:
        if h["id"] == hid:
            return h
    return None


def _verb_of(effect: dict) -> str:
    verbs = set(effect) & VERBS
    unknown = set(effect) - VERBS - MODIFIERS
    if unknown:
        raise EffectError(f"unknown effect key(s): {sorted(unknown)}")
    if len(verbs) != 1:
        raise EffectError(f"effect must contain exactly one verb, got {sorted(verbs)}")
    return verbs.pop()


def _amount(value, ctx, dice) -> int:
    return dice.roll(value, ctx["mods"])


# --- verb handlers ------------------------------------------------------------

def _do_damage_hazard(state, effect, ctx, dice, changes):
    hazard = _hazard(state)
    if hazard is None:
        return
    spec = effect["damage_hazard"]
    if isinstance(spec, dict):
        if "fraction" in spec:
            amount = int(hazard["severity"] * spec["fraction"])
        elif "per_party_focus" in spec:
            pool = sum(c["focus"] for c in state["characters"].values())
            amount = pool * int(spec["per_party_focus"])
        else:
            raise EffectError(f"unsupported damage_hazard form: {sorted(spec)}")
    else:
        amount = _amount(spec, ctx, dice)

    weakness_stat = effect.get("double_if_weakness")
    if weakness_stat and hazard.get("weakness") == weakness_stat:
        amount *= 2
    last = state.get("last_ability_id")
    current = ctx.get("ability").id if ctx.get("ability") else None
    if effect.get("bonus_if_repeated") and last is not None and last == current:
        amount = int(amount * (1 + effect["bonus_if_repeated"]))
    if ctx.get("crit"):
        amount *= 2

    amount = max(0, amount)
    # A gate boss may refuse chip damage; the floor is applied last, after every
    # multiplier, so it reads the hit the hazard actually takes.
    amount = damage_floor(hazard, amount)
    hazard["severity"] = max(0, hazard["severity"] - amount)
    if hazard["severity"] == 0:
        hazard["defeated"] = True
    changes.append({"kind": "hazard_damage", "hazard_id": hazard["id"], "amount": amount,
                    "severity": hazard["severity"]})


def _do_party(state, effect, ctx, dice, changes):
    for field, delta in effect["party"].items():
        value = int(delta)
        if ctx.get("crit") and value > 0 and field not in _PENALTY_FIELDS:
            value *= 2
        state["party"][field] = state["party"][field] + value
        if field == "tech_debt":
            state["party"][field] = max(0, state["party"][field])
        changes.append({"kind": "party_delta", "field": field, "delta": value,
                        "value": state["party"][field]})


def _do_heal_ally(state, effect, ctx, dice, changes):
    pid = ctx.get("target_id") or ctx["actor_id"]
    char = state["characters"][pid]
    amount = _amount(effect["heal_ally"], ctx, dice)
    if ctx.get("crit"):
        amount *= 2
    before = char["stamina"]
    char["stamina"] = min(char["max_stamina"], before + amount)
    changes.append({"kind": "heal", "player_id": pid,
                    "amount": char["stamina"] - before, "stamina": char["stamina"]})


def _do_restore_focus(state, effect, ctx, dice, changes):
    spec = effect["restore_focus"]
    amount = _amount(spec["amount"], ctx, dice)
    if ctx.get("crit"):
        amount *= 2
    if spec.get("scope") == "party":
        targets = list(state["characters"])
    else:
        targets = [ctx.get("target_id") or ctx["actor_id"]]
    for pid in targets:
        char = state["characters"][pid]
        before = char["focus"]
        char["focus"] = min(char["max_focus"], before + amount)
        changes.append({"kind": "focus_restore", "player_id": pid,
                        "amount": char["focus"] - before, "focus": char["focus"]})


def _do_stress_self(state, effect, ctx, dice, changes):
    char = state["characters"][ctx["actor_id"]]
    amount = max(0, _amount(effect["stress_self"], ctx, dice))
    char["stamina"] = max(0, char["stamina"] - amount)
    changes.append({"kind": "stress", "player_id": char["player_id"], "amount": amount,
                    "stamina": char["stamina"]})


def _do_apply_condition(state, effect, ctx, dice, changes):
    spec = effect["apply_condition"]
    target = None if spec.get("scope") in ("party", "hazard") else \
        (ctx.get("target_id") or ctx["actor_id"])
    add_condition(state, spec["condition"], spec.get("scope", "party"),
                  int(spec["value"]), int(spec["rounds"]), target)
    changes.append({"kind": "condition", "name": spec["condition"],
                    "value": int(spec["value"]), "rounds": int(spec["rounds"]),
                    "scope": spec.get("scope", "party")})


def _do_reveal(state, effect, ctx, dice, changes):
    hazard = _hazard(state)
    if hazard is None:
        return
    what = effect["reveal"].get("what", "weakness")
    fields = ["weakness", "dc", "next_attack"] if what == "all" else [what]
    revealed = set(hazard.get("revealed", [])) | set(fields)
    hazard["revealed"] = sorted(revealed)
    changes.append({"kind": "reveal", "hazard_id": hazard["id"], "fields": fields})


def _do_reroll_grant(state, effect, ctx, dice, changes):
    spec = effect["reroll_grant"]
    pid = ctx.get("target_id") or ctx["actor_id"]
    add_condition(state, "reroll", "ally", int(spec.get("count", 1)), 2, pid)
    changes.append({"kind": "reroll", "player_id": pid,
                    "count": int(spec.get("count", 1))})


def _do_shield(state, effect, ctx, dice, changes):
    spec = effect["shield"]
    amount = _amount(spec["amount"], ctx, dice)
    scope = spec.get("scope", "party")
    target = None if scope == "party" else (ctx.get("target_id") or ctx["actor_id"])
    add_condition(state, "shield", scope, amount, int(spec.get("rounds", 1)), target)
    changes.append({"kind": "shield", "amount": amount, "scope": scope})


def _do_skip_hazard(state, effect, ctx, dice, changes):
    hazard = _hazard(state)
    if hazard is None:
        return
    hazard["defeated"] = True
    multiplier = float(effect["skip_hazard"].get("return_multiplier", 1.5))
    returning = dict(hazard)
    returning.update({
        "id": f"{hazard['id']}_returned",
        "name": f"{hazard['name']} (Deferred)",
        "phase_index": min(4, hazard["phase_index"] + 1),
        "max_severity": math.ceil(hazard["max_severity"] * multiplier),
        "severity": math.ceil(hazard["max_severity"] * multiplier),
        "defeated": False,
        "revealed": [],
    })
    state["hazards"].append(returning)
    changes.append({"kind": "hazard_skipped", "hazard_id": hazard["id"],
                    "returns_as": returning["id"],
                    "returns_in_phase": returning["phase_index"]})


def _do_copy_ability(state, effect, ctx, dice, changes):
    add_condition(state, "borrowed_ability", "ally", 1, 2, ctx["actor_id"])
    changes.append({"kind": "ability_borrowed", "player_id": ctx["actor_id"]})


_HANDLERS = {
    "damage_hazard": _do_damage_hazard,
    "party": _do_party,
    "heal_ally": _do_heal_ally,
    "restore_focus": _do_restore_focus,
    "stress_self": _do_stress_self,
    "apply_condition": _do_apply_condition,
    "reveal": _do_reveal,
    "reroll_grant": _do_reroll_grant,
    "shield": _do_shield,
    "skip_hazard": _do_skip_hazard,
    "copy_ability": _do_copy_ability,
}


def apply_effects(state: dict, effects: Sequence[dict], ctx: dict,
                  dice: Dice) -> list:
    """Apply each effect to `state` in order, returning the change records."""
    changes: list = []
    for effect in effects:
        _HANDLERS[_verb_of(effect)](state, effect, ctx, dice, changes)
    return changes
