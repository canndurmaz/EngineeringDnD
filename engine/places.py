"""The office: where each engineer stands, and what standing there lets them do.

Pure, like the rest of engine/. The service stores a character's zone and desk
and calls in here to decide whether a place action is legal and what it does.
Nothing in this module rolls dice: a place action is a sure thing, paid for with
the turn rather than with Focus.

Zones are server-authoritative. A client animates a walk however it likes, but
the only position the rules ever read is the zone string stored on the
character -- ``office_zone`` -- which is one of the fixed rooms below or
``desk:<player_id>`` for somebody's desk.
"""
from __future__ import annotations

from engine.effects import add_condition
from engine.rules import RuleError

FLOOR = "floor"
LAB = "lab"
BREAK_ROOM = "break_room"
WHITEBOARD = "whiteboard"
DESK_PREFIX = "desk:"

#: The rooms every office has, whoever is seated.
FIXED_ZONES = (FLOOR, LAB, BREAK_ROOM, WHITEBOARD)

#: What each place action is called, and where it can be done. "desk" means
#: somebody else's desk; "own_desk" means the actor's own.
PLACE_ACTIONS = {
    "bench_test": {"name": "Bench Test", "zone": LAB},
    "coffee_break": {"name": "Coffee Break", "zone": BREAK_ROOM},
    "pair_up": {"name": "Pair Up", "zone": "desk"},
}

#: How many coffee breaks one engineer gets per phase.
COFFEE_LIMIT = 2

#: The roll bonuses the lab and a pairing session hand out.
BENCH_BONUS = 2
PAIR_BONUS = 1

#: Desk customisation. The first entry of each list is the default, and
#: anything not on a list falls back to it.
DESK_OPTIONS = {
    "desk_color": ["oak", "walnut", "white", "graphite", "teal", "rust"],
    "monitor": ["single", "dual", "laptop"],
    "plant": ["none", "succulent", "fern"],
    "mug": ["none", "coffee", "tea"],
    "poster": ["none", "gantt", "schematic", "motivational"],
}
DESK_DEFAULTS = {kind: values[0] for kind, values in DESK_OPTIONS.items()}


class PlaceError(RuleError):
    """An illegal place action. A RuleError, so it surfaces as a 400."""


# --- zones -------------------------------------------------------------------

def desk_zone(player_id: str) -> str:
    return f"{DESK_PREFIX}{player_id}"


def desk_owner(zone) -> "str | None":
    """The player whose desk this zone is, or None for any other zone."""
    if isinstance(zone, str) and zone.startswith(DESK_PREFIX):
        return zone[len(DESK_PREFIX):] or None
    return None


def zone_of(char: dict) -> str:
    return char.get("office_zone") or FLOOR


def desk_assignments(state: dict) -> list:
    """One desk per seated character, in seat order.

    Seat order is the turn order: both are the order people sat down, and the
    turn order is the one the room already persists.
    """
    seated = [pid for pid in state["turn"]["order"] if pid in state["characters"]]
    seated += sorted(pid for pid in state["characters"] if pid not in seated)
    return [{"index": i, "player_id": pid, "zone": desk_zone(pid)}
            for i, pid in enumerate(seated)]


def zones(state: dict) -> list:
    return list(FIXED_ZONES) + [d["zone"] for d in desk_assignments(state)]


def is_zone(state: dict, zone) -> bool:
    if not isinstance(zone, str):
        return False
    if zone in FIXED_ZONES:
        return True
    owner = desk_owner(zone)
    return owner is not None and owner in state["characters"]


def action_here(state: dict, player_id: str) -> "str | None":
    """The place action the player's current zone offers, or None.

    "customize" is what the player's own desk offers; it is cosmetic and never
    goes through resolve_place.
    """
    char = state["characters"].get(player_id)
    if char is None:
        return None
    zone = zone_of(char)
    if zone == LAB:
        return "bench_test"
    if zone == BREAK_ROOM:
        return "coffee_break"
    owner = desk_owner(zone)
    if owner == player_id:
        return "customize"
    if owner is not None:
        return "pair_up"
    return None


# --- desks -------------------------------------------------------------------

def normalise_desk(raw) -> dict:
    """A complete desk dict. Unknown keys are dropped; unknown values fall back."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for kind, values in DESK_OPTIONS.items():
        value = raw.get(kind)
        out[kind] = value if isinstance(value, str) and value in values \
            else DESK_DEFAULTS[kind]
    return out


# --- turn arithmetic ---------------------------------------------------------

def rounds_until_next_turn(state: dict, player_id: str) -> int:
    """How many round-ends a condition must survive to reach this player's
    next roll.

    Conditions tick down at the end of every round. A player still to act this
    round rolls before the next tick, so one round is enough; a player who has
    already acted (the actor included) rolls after it, so it takes two.
    """
    turn = state["turn"]
    order = turn["order"]
    if player_id not in order:
        return 2
    return 1 if order.index(player_id) > turn["turn_index"] else 2


def _grant_roll_bonus(state: dict, player_id: str, value: int,
                      changes: list) -> None:
    rounds = rounds_until_next_turn(state, player_id)
    add_condition(state, "roll_bonus", "ally", value, rounds, player_id)
    changes.append({"kind": "condition", "name": "roll_bonus", "value": value,
                    "rounds": rounds, "scope": "ally", "player_id": player_id})


# --- place actions -----------------------------------------------------------

def _active_hazard(state: dict):
    hid = state.get("active_hazard_id")
    return next((h for h in state["hazards"] if h["id"] == hid), None)


def _round_key(state: dict) -> str:
    return f"round:{state['turn']['round']}"


def validate_place(state: dict, actor_id: str, action: str,
                   target_id: "str | None" = None) -> None:
    """Raise PlaceError unless `actor_id` may take `action` right now."""
    if action not in PLACE_ACTIONS:
        raise PlaceError(f"unknown place action: {action}")
    char = state["characters"].get(actor_id)
    if char is None:
        raise PlaceError("You are not seated in this room.")
    order = state["turn"]["order"]
    if not order or order[state["turn"]["turn_index"]] != actor_id:
        raise PlaceError("It is not your turn.")
    if char["stamina"] <= 0:
        raise PlaceError("You are Burned Out and cannot act.")
    zone = zone_of(char)
    name = PLACE_ACTIONS[action]["name"]

    if action == "bench_test":
        if zone != LAB:
            raise PlaceError(f"{name} needs you in the lab.")
        return

    if action == "coffee_break":
        if zone != BREAK_ROOM:
            raise PlaceError(f"{name} needs you in the break room.")
        if int(char.get("coffee_used") or 0) >= COFFEE_LIMIT:
            raise PlaceError(
                f"You have had {COFFEE_LIMIT} coffee breaks this phase already.")
        return

    # pair_up
    owner = desk_owner(zone)
    if owner is None or owner == actor_id:
        raise PlaceError(f"{name} needs you at a colleague's desk.")
    if target_id and target_id != owner:
        raise PlaceError(f"{name} pairs you with the owner of this desk.")
    partner = state["characters"].get(owner)
    if partner is None:
        raise PlaceError("Nobody sits at this desk any more.")
    if partner["stamina"] <= 0:
        raise PlaceError(f"{partner['name']} is Burned Out and cannot pair.")
    if char.get("used", {}).get("pair_up") == _round_key(state):
        raise PlaceError(f"{name} can only be used once per round.")


def resolve_place(state: dict, actor_id: str, action: str,
                  target_id: "str | None" = None) -> list:
    """Validate, apply, and return the change records (same shapes as
    engine.effects produces, so the log can render them unchanged)."""
    validate_place(state, actor_id, action, target_id)
    char = state["characters"][actor_id]
    changes: list = []

    if action == "bench_test":
        hazard = _active_hazard(state)
        if hazard is not None and "weakness" not in hazard.get("revealed", []):
            hazard["revealed"] = sorted(set(hazard.get("revealed", []))
                                        | {"weakness"})
            changes.append({"kind": "reveal", "hazard_id": hazard["id"],
                            "fields": ["weakness"]})
        else:
            _grant_roll_bonus(state, actor_id, BENCH_BONUS, changes)
        return changes

    if action == "coffee_break":
        amount = max(2, char["max_stamina"] // 4)
        before = char["stamina"]
        char["stamina"] = min(char["max_stamina"], before + amount)
        char["coffee_used"] = int(char.get("coffee_used") or 0) + 1
        changes.append({"kind": "heal", "player_id": actor_id,
                        "amount": char["stamina"] - before,
                        "stamina": char["stamina"]})
        changes.append({"kind": "coffee", "player_id": actor_id,
                        "used": char["coffee_used"], "limit": COFFEE_LIMIT})
        return changes

    # pair_up
    owner = desk_owner(zone_of(char))
    char.setdefault("used", {})["pair_up"] = _round_key(state)
    _grant_roll_bonus(state, actor_id, PAIR_BONUS, changes)
    _grant_roll_bonus(state, owner, PAIR_BONUS, changes)
    return changes


def reset_phase(state: dict) -> None:
    """A new phase refills everyone's coffee allowance."""
    for char in state["characters"].values():
        if char.get("coffee_used"):
            char["coffee_used"] = 0
