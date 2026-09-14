"""The single d20 mechanic, turn order, and the hazard's response."""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.classes import Ability
from engine.dice import Dice
from engine.effects import (active_conditions, add_condition, apply_effects,
                            condition_total)


class RuleError(Exception):
    """An illegal move. Surfaces to the player as a 400, never a crash."""


@dataclass
class ActionResult:
    natural: int
    stat_used: str
    stat_mod: int
    roll_bonus: int
    total: int
    dc: int
    outcome: str
    changes: list = field(default_factory=list)
    rerolled: bool = False


def stat_mod(score: int) -> int:
    return (score - 10) // 2


def _hazard(state):
    hid = state.get("active_hazard_id")
    for h in state["hazards"]:
        if h["id"] == hid:
            return h
    return None


def _active_player_id(state) -> str:
    turn = state["turn"]
    return turn["order"][turn["turn_index"]]


def _stat_for(state, actor_id, ability: Ability) -> tuple:
    stats = state["characters"][actor_id]["stats"]
    best, best_mod = ability.stat, stat_mod(stats[ability.stat])
    if ability.stat_alt and stat_mod(stats[ability.stat_alt]) > best_mod:
        best, best_mod = ability.stat_alt, stat_mod(stats[ability.stat_alt])
    return best, best_mod


def effective_dc(state, ability: Ability, actor_id: str) -> int:
    hazard = _hazard(state)
    base = hazard["dc"] if hazard else 12
    debt_penalty = state["party"]["tech_debt"] // 10
    delta = condition_total(state, "dc_delta", actor_id)
    return base + ability.dc_mod + debt_penalty + delta


def _once_per_key(state, ability: Ability) -> "str | None":
    if ability.once_per == "hazard":
        return f"hazard:{state.get('active_hazard_id')}"
    if ability.once_per == "phase":
        return f"phase:{state['room']['phase_index']}"
    return None


def validate_action(state, actor_id: str, ability: Ability) -> None:
    if actor_id not in state["characters"]:
        raise RuleError("You are not seated in this room.")
    char = state["characters"][actor_id]
    if _active_player_id(state) != actor_id:
        raise RuleError("It is not your turn.")
    if char["stamina"] <= 0:
        raise RuleError("You are Burned Out and cannot act.")
    borrowed = condition_total(state, "borrowed_ability", actor_id) > 0
    if ability.id not in char["unlocked"] and not borrowed:
        raise RuleError(f"{ability.name} is not unlocked for you.")
    if char["focus"] < ability.focus_cost:
        raise RuleError(
            f"{ability.name} needs {ability.focus_cost} Focus, you have {char['focus']}.")
    for resource, cost in ability.extra_cost.items():
        if state["party"][resource] < cost:
            raise RuleError(
                f"{ability.name} costs {cost} {resource.title()}, "
                f"the party has {state['party'][resource]}.")
    key = _once_per_key(state, ability)
    if key and char["used"].get(ability.id) == key:
        raise RuleError(f"{ability.name} can only be used once per {ability.once_per}.")


def resolve_action(state, actor_id: str, ability: Ability, dice: Dice,
                   target_id: "str | None" = None) -> ActionResult:
    validate_action(state, actor_id, ability)
    char = state["characters"][actor_id]
    hazard = _hazard(state)

    # Pay costs first; they are spent whether or not the roll lands.
    char["focus"] -= ability.focus_cost
    for resource, cost in ability.extra_cost.items():
        state["party"][resource] -= cost
    key = _once_per_key(state, ability)
    if key:
        char["used"][ability.id] = key
    borrowed = [c for c in active_conditions(state, "borrowed_ability", actor_id)]
    if ability.id not in char["unlocked"] and borrowed:
        state["conditions"].remove(borrowed[0])

    stat_used, mod = _stat_for(state, actor_id, ability)
    bonus = condition_total(state, "roll_bonus", actor_id)
    bonus += condition_total(state, "next_attack_bonus", actor_id)
    if hazard and "weakness" in hazard.get("revealed", []) \
            and hazard.get("weakness") == stat_used:
        bonus += 2
    dc = effective_dc(state, ability, actor_id)

    rerolled = False
    if ability.fixed_roll is not None:
        natural = ability.fixed_roll
    else:
        natural = dice.d20()
        if natural + mod + bonus < dc:
            rerolls = active_conditions(state, "reroll", actor_id)
            if rerolls:
                state["conditions"].remove(rerolls[0])
                natural = dice.d20()
                rerolled = True

    crit_values = [c["value"] for c in active_conditions(state, "crit_range", actor_id)]
    crit_floor = min(crit_values) if crit_values else 20   # stacking must not cancel
    total = natural + mod + bonus

    if ability.fixed_roll is None and natural == 1 and not ability.no_fumble:
        cancels = active_conditions(state, "cancel_fumble", actor_id)
        if cancels:
            state["conditions"].remove(cancels[0])
            outcome = "failure"
        else:
            outcome = "fumble"
    elif ability.fixed_roll is None and natural >= crit_floor and not ability.no_crit:
        outcome = "crit"
    else:
        outcome = "success" if total >= dc else "failure"

    ctx = {"actor_id": actor_id, "target_id": target_id,
           "mods": {k: stat_mod(v) for k, v in char["stats"].items()},
           "crit": outcome == "crit", "ability": ability}
    effects = ability.on_success if outcome in ("success", "crit") else ability.on_fail
    changes = apply_effects(state, list(effects), ctx, dice)

    # Consume single-use attack bonuses now that the roll is spent.
    for c in list(active_conditions(state, "next_attack_bonus", actor_id)):
        state["conditions"].remove(c)

    if outcome == "fumble":
        changes.append(hazard_attack(state, dice, forced_target=actor_id))

    state["last_ability_id"] = ability.id
    return ActionResult(natural=natural, stat_used=stat_used, stat_mod=mod,
                        roll_bonus=bonus, total=total, dc=dc, outcome=outcome,
                        changes=changes, rerolled=rerolled)


def hazard_attack(state, dice: Dice, forced_target: "str | None" = None) -> dict:
    """The hazard's response. Attack type is fixed per hazard, never random."""
    hazard = _hazard(state)
    if hazard is None or hazard.get("defeated"):
        return {"kind": "hazard_attack", "blocked": "no_hazard"}
    if condition_total(state, "stunned") > 0:
        for c in list(active_conditions(state, "stunned")):
            state["conditions"].remove(c)
        return {"kind": "hazard_attack", "blocked": "stunned"}

    phase = state["room"]["phase_index"]
    attack = hazard["attack_type"]
    # The party is about to watch this attack land (and the payload names it, which
    # is diegetic -- you see what hit you). Record that as a reveal so the UI stops
    # claiming "next: unknown" for something everyone just witnessed; otherwise the
    # reveal state would lie about what the party knows.
    hazard["revealed"] = sorted(set(hazard.get("revealed", [])) | {"next_attack"})

    if attack == "stress":
        living = [p for p, c in state["characters"].items() if c["stamina"] > 0]
        target = forced_target if forced_target in living else (
            dice.choice(sorted(living)) if living else None)
        if target is None:
            return {"kind": "hazard_attack", "blocked": "party_down"}
        amount = dice.roll(f"1d6+{phase + 1}", {})
        shields = active_conditions(state, "shield", target)
        absorbed = 0
        for shield in shields:
            take = min(shield["value"], amount - absorbed)
            shield["value"] -= take
            absorbed += take
            if shield["value"] <= 0:
                state["conditions"].remove(shield)
            if absorbed >= amount:
                break
        net = amount - absorbed
        char = state["characters"][target]
        char["stamina"] = max(0, char["stamina"] - net)
        return {"kind": "hazard_attack", "attack": "stress", "player_id": target,
                "amount": net, "absorbed": absorbed, "stamina": char["stamina"]}

    if attack in ("burn_budget", "burn_schedule"):
        if condition_total(state, "resist_burn") > 0:
            return {"kind": "hazard_attack", "blocked": "resist_burn"}
        field_name = "budget" if attack == "burn_budget" else "schedule"
        amount = dice.roll(f"1d6+{phase + 2}", {})
        state["party"][field_name] -= amount
        return {"kind": "hazard_attack", "attack": attack, "field": field_name,
                "amount": amount, "value": state["party"][field_name]}

    if attack == "debt":
        if condition_total(state, "no_debt") > 0:
            return {"kind": "hazard_attack", "blocked": "no_debt"}
        amount = dice.roll("1d4", {}) + phase
        state["party"]["tech_debt"] += amount
        return {"kind": "hazard_attack", "attack": "debt", "amount": amount,
                "value": state["party"]["tech_debt"]}

    raise RuleError(f"unknown hazard attack type: {attack!r}")


def advance_turn(state) -> bool:
    """Move to the next living player. Returns True when a round completed."""
    turn = state["turn"]
    order = turn["order"]
    completed = False
    for _ in range(len(order)):
        turn["turn_index"] += 1
        if turn["turn_index"] >= len(order):
            turn["turn_index"] = 0
            turn["round"] += 1
            completed = True
        candidate = order[turn["turn_index"]]
        if state["characters"][candidate]["stamina"] > 0:
            break
    char = state["characters"][order[turn["turn_index"]]]
    char["focus"] = min(char["max_focus"], char["focus"] + 1)
    return completed
