"""Computer-controlled engineers: what a bot does, and when it does it.

Two things live here and nothing else:

* `choose_action` -- a pure function over a state snapshot. It reads what the
  party needs and names one ability (and target), or None to pass. It is the
  whole of bot "intelligence" and has no clock, no database and no thread.
* `BotRunner` -- a daemon thread, shaped like narrator.worker.NarrationWorker,
  that notices when the active seat belongs to a bot and plays it.

This module is deliberately *outside* engine/. The engine is the ruleset: what
is legal and what a roll does. A policy is an opinion about which legal move is
best, and opinions do not belong in the rules. bots.py imports engine; engine
never imports bots.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

from engine.classes import Ability, Catalog
from engine.effects import condition_total
from engine.rules import RuleError, validate_action

log = logging.getLogger(__name__)

# The Technical Debt score at which every DC starts carrying +1 (see
# engine.rules.effective_dc: the penalty is tech_debt // 10).
DEBT_THRESHOLD = 10

_DICE = re.compile(r"^(?:(\d+)d(\d+))?(?:([+-])([A-Za-z_]+|\d+))?$")


# --- reading an ability ------------------------------------------------------

def _effects(ability: Ability) -> tuple:
    """Only on_success matters to a policy: a bot picks for what it wants to
    happen, not for the consolation prize when the roll misses."""
    return tuple(ability.on_success)


def _heals(ability: Ability) -> bool:
    return any("heal_ally" in effect for effect in _effects(ability))


def _cuts_debt(ability: Ability) -> bool:
    for effect in _effects(ability):
        delta = effect.get("party", {}).get("tech_debt")
        if isinstance(delta, (int, float)) and delta < 0:
            return True
    return False


def _reveals(ability: Ability) -> bool:
    return any("reveal" in effect for effect in _effects(ability))


def _party_buffs(ability: Ability) -> list:
    """The condition names this ability would apply to the whole party."""
    out = []
    for effect in _effects(ability):
        spec = effect.get("apply_condition")
        if isinstance(spec, dict) and spec.get("scope") == "party":
            out.append(spec["condition"])
    return out


# --- expected damage ---------------------------------------------------------

def _dice_mean(expr, mods: dict) -> float:
    """The mean of a dice expression: n*(faces+1)/2 plus the modifier.

    Same grammar engine.dice.Dice.roll accepts, read rather than rolled -- a
    policy must not consume the room's RNG to make up its mind.
    """
    if isinstance(expr, (int, float)):
        return float(expr)
    text = str(expr).replace(" ", "")
    if re.fullmatch(r"-?\d+", text):
        return float(text)
    match = _DICE.fullmatch(text)
    if not match:
        return 0.0
    count, faces, sign, term = match.groups()
    total = 0.0
    if count:
        total = int(count) * (int(faces) + 1) / 2
    if term is not None:
        value = int(term) if term.isdigit() else mods.get(term, 0)
        total += value if sign == "+" else -value
    return total


def expected_damage(state: dict, ability: Ability, mods: dict) -> float:
    """How much severity this ability is worth on average, on this board."""
    hazard = _active_hazard(state)
    total = 0.0
    for effect in _effects(ability):
        if "damage_hazard" not in effect:
            continue
        spec = effect["damage_hazard"]
        if isinstance(spec, dict):
            if "fraction" in spec:
                total += (hazard["severity"] if hazard else 0) * spec["fraction"]
            elif "per_party_focus" in spec:
                pool = sum(c["focus"] for c in state["characters"].values())
                total += pool * int(spec["per_party_focus"])
        else:
            total += _dice_mean(spec, mods)
    return total


# --- board reading -----------------------------------------------------------

def _active_hazard(state: dict):
    hid = state.get("active_hazard_id")
    for hazard in state["hazards"]:
        if hazard["id"] == hid:
            return hazard
    return None


def _mods(char: dict) -> dict:
    return {stat: (score - 10) // 2 for stat, score in char["stats"].items()}


def _legal_abilities(state: dict, player_id: str, catalog: Catalog) -> list:
    """Every ability this bot may actually play right now.

    The test is engine.rules.validate_action itself, not a second copy of its
    conditions: unlocked, affordable in Focus and in party resources, not a
    spent once_per, not burned out, and its turn. Re-implementing that list here
    is how a bot ends up proposing a move the server then rejects.
    """
    char = state["characters"][player_id]
    out = []
    for ability in catalog.abilities_for(char["class_id"]):
        try:
            validate_action(state, player_id, ability)
        except RuleError:
            continue
        out.append(ability)
    return sorted(out, key=lambda a: a.id)


def _most_hurt(state: dict, player_id: str) -> "str | None":
    """The ally in the worst shape, as a fraction of their own max stamina."""
    hurt = [(c["stamina"] / max(1, c["max_stamina"]), c["stamina"], pid)
            for pid, c in state["characters"].items()
            if c["stamina"] * 2 < c["max_stamina"]]
    if not hurt:
        return None
    return min(hurt)[2]


# --- the policy --------------------------------------------------------------

def decide(state: dict, player_id: str,
           catalog: Catalog) -> "tuple[str, str | None, str]":
    """The policy, and the branch it took: (ability_id, target_id, branch).

    Priorities, in order: keep people standing, keep the debt below the DC
    penalty, learn the weakness, buff the party, then hit the problem. Pure: it
    reads the snapshot and returns a choice, mutating nothing.

    The branch name is the reason for the move, and it is what the bot says out
    loud afterwards. It is returned rather than re-derived because a second
    reading of the board could disagree with the first and put the wrong line in
    the bot's mouth. A pass is the branch "pass" with no ability.
    """
    char = state["characters"].get(player_id)
    if char is None:
        return None, None, "pass"
    options = _legal_abilities(state, player_id, catalog)
    if not options:
        return None, None, "pass"       # nothing affordable; pass the turn

    # 1. Somebody is below half stamina and this bot can heal.
    wounded = _most_hurt(state, player_id)
    if wounded is not None:
        healers = [a for a in options if _heals(a)]
        if healers:
            return healers[0].id, wounded, "heal"

    # 2. Technical Debt is taxing every roll in the party.
    if state["party"]["tech_debt"] >= DEBT_THRESHOLD:
        menders = [a for a in options if _cuts_debt(a)]
        if menders:
            return menders[0].id, None, "debt"

    # 3. Nobody knows the weakness yet, and knowing it is +2 for everyone.
    hazard = _active_hazard(state)
    # `revealed` holds field names, not stat names -- "weakness" being in it is
    # what engine.rules checks before granting the +2.
    if hazard is not None and "weakness" not in hazard.get("revealed", []):
        scouts = [a for a in options if _reveals(a)]
        if scouts:
            return scouts[0].id, None, "reveal"

    # 4. A party buff nobody is running yet.
    for ability in options:
        for condition in _party_buffs(ability):
            if condition_total(state, condition) == 0:
                return ability.id, None, "buff"

    # 5. Otherwise hit it, hardest first. Ties break on id so a bot facing the
    #    same board twice makes the same choice twice.
    mods = _mods(char)
    best = min(options, key=lambda a: (-expected_damage(state, a, mods), a.id))
    return best.id, None, "attack"


def choose_action(state: dict, player_id: str,
                  catalog: Catalog) -> "tuple[str, str | None] | None":
    """`decide` without the reason: (ability_id, target_id), or None to pass."""
    ability_id, target_id, _ = decide(state, player_id, catalog)
    return None if ability_id is None else (ability_id, target_id)


# --- what a bot says about it ------------------------------------------------

#: One short line per branch of `decide`, in the register of somebody who has
#: done this before and would rather be doing something else. Several per branch
#: so a table of bots does not read like a stuck tape.
BOT_LINES = {
    "heal": [
        "You're taking too much of this. Patching you up.",
        "Hold still. You're no use to anyone at zero.",
        "Stopping the bleeding, then we carry on.",
    ],
    "debt": [
        "We're carrying too much debt. Cleaning up.",
        "Every roll is taxed until this is paid down.",
        "The shortcuts are invoicing us. Settling some.",
    ],
    "reveal": [
        "Scoping this before we swing at it.",
        "Measuring first. I'd like to know what we're hitting.",
        "Getting data on it. Guessing is the expensive option.",
    ],
    "buff": [
        "Setting the team up before the next push.",
        "Putting some process behind this one.",
    ],
    "attack": [
        "Straight at it, then.",
        "Nothing clever left. Applying pressure.",
        "This is the largest thing I can do to it.",
    ],
    "pass": [
        "Nothing I can afford this turn. Passing.",
        "I've got nothing useful here. Over to you.",
    ],
}


def turn_number(state: dict) -> int:
    """A counter that goes up by one every turn, for rotating the lines.

    Rounds restart the index, so the round has to be folded in or the same seat
    would get the same line every round.
    """
    turn = state.get("turn") or {}
    order = turn.get("order") or []
    return max(0, int(turn.get("round", 1)) - 1) * max(1, len(order)) \
        + int(turn.get("turn_index", 0))


def bot_line(branch: str, turn: int) -> str:
    """The line for this branch on this turn.

    Rotation, not choice: Random(n).choice() over a three-item list returns the
    same element for runs of consecutive n, which is how a bot ends up saying
    the same sentence three turns running. Same reasoning as
    narrator.fallback._pick, and the same fix.
    """
    options = BOT_LINES.get(branch) or BOT_LINES["pass"]
    return options[turn % len(options)]


# --- running the turn --------------------------------------------------------

def _default_delay() -> float:
    try:
        return max(0.0, float(os.environ.get("CP_BOT_DELAY", "2.0")))
    except ValueError:
        return 2.0


class BotRunner:
    """Polls active rooms and plays any seat that belongs to a bot.

    Shaped like NarrationWorker on purpose: one daemon thread, a stop event,
    and every tick wrapped so that one sick room cannot take the thread down.

    The delay is not politeness -- a bot that answers in the same millisecond
    the previous roll landed reads as a glitch, and the log scrolls past before
    anyone can follow it. The state is already committed either way.
    """

    def __init__(self, service, catalog: Catalog, delay: "float | None" = None,
                 interval: float = 0.5) -> None:
        self.service = service
        self.catalog = catalog
        self.delay = _default_delay() if delay is None else max(0.0, float(delay))
        self.interval = interval
        self.thread: "threading.Thread | None" = None
        self._stop = threading.Event()
        # room_id -> (turn key, when this runner first saw that turn)
        self._waiting: dict = {}

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="bots", daemon=True)
        self.thread.start()

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=join_timeout)
            self.thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.interval)

    # --- one poll ----------------------------------------------------------

    def run_once(self, now: "float | None" = None) -> int:
        """One sweep of every active room. Returns how many bot turns were taken.

        Exposed so tests can drive the policy end to end without a thread and
        without waiting on a clock.
        """
        now = time.monotonic() if now is None else now
        taken = 0
        for row in self._active_rooms():
            room_id = row.get("room_id")
            try:
                taken += self._tick(room_id, now)
            except Exception:           # one bad room must not kill the thread
                log.exception("bot tick failed for room %s", room_id)
        return taken

    def _active_rooms(self) -> list:
        try:
            return [r for r in self.service.list_rooms()
                    if r.get("status") == "active"]
        except Exception:
            log.exception("bot runner could not list rooms")
            return []

    def _tick(self, room_id: str, now: float) -> int:
        # The whole table waits for the DM, and a bot is part of the table. It
        # simply declines this tick and is picked up by the next one at the
        # usual interval -- no busy loop, no second clock.
        if self._gated(room_id):
            return 0
        state = self.service.snapshot(room_id)
        if state["room"]["status"] != "active":
            self._waiting.pop(room_id, None)
            return 0
        turn = state["turn"]
        order = turn["order"]
        if not order:
            return 0
        player_id = order[turn["turn_index"] % len(order)]
        char = state["characters"].get(player_id)
        if char is None or not char.get("is_bot"):
            self._waiting.pop(room_id, None)
            return 0

        # One beat per turn, so the table can read what just happened. The key
        # changes the moment the turn does, which restarts the wait.
        key = (player_id, turn["round"], turn["turn_index"])
        seen_key, since = self._waiting.get(room_id, (None, None))
        if seen_key != key:
            self._waiting[room_id] = (key, now)
            since = now
        if now - since < self.delay:
            return 0
        self._waiting.pop(room_id, None)

        self._play(room_id, player_id, state)
        return 1

    def _gated(self, room_id: str) -> bool:
        gate = getattr(self.service, "dm_gate", None)
        if not callable(gate):
            return False
        try:
            return bool(gate(room_id).get("waiting"))
        except Exception:
            log.exception("bot runner could not read the DM gate for %s", room_id)
            return False

    def _say(self, room_id: str, player_id: str, state: dict,
             branch: str) -> None:
        """One line of table talk explaining the move it is about to make.

        Best effort: a bot that cannot get a word in still takes its turn. The
        chat is commentary, and commentary must never be able to wedge the game.
        """
        try:
            self.service.post_chat(room_id, player_id,
                                   bot_line(branch, turn_number(state)))
        except Exception:
            log.info("bot %s could not speak", player_id, exc_info=True)

    def _play(self, room_id: str, player_id: str, state: dict) -> None:
        ability_id, target_id, branch = None, None, "pass"
        if state["characters"][player_id]["stamina"] > 0:
            ability_id, target_id, branch = decide(state, player_id, self.catalog)
        # Said before the roll, because it is the reason for the move and not a
        # report of how it went -- that is the DM's job.
        self._say(room_id, player_id, state, branch)
        if ability_id is None:
            self.service.end_turn(room_id, player_id)
            return
        try:
            self.service.act(room_id, player_id, ability_id, target_id)
        except Exception:
            # The board moved between the snapshot and the act (a human's turn
            # landed first, a phase gate fired). Passing keeps the table moving
            # rather than wedging the round on a bot that cannot decide.
            log.info("bot %s could not play %s; passing", player_id, ability_id,
                     exc_info=True)
            self.service.end_turn(room_id, player_id)
