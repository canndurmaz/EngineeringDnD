"""Orchestration: per-room locks, and the seam between pure engine and storage."""
from __future__ import annotations

import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import appearance as appearance_lib
from engine.character import new_character_detailed
from engine.classes import STATS, Catalog
from engine.dice import Dice
from engine.effects import expire_conditions
from engine import places
from engine.phases import (PHASES, advance_phase, build_campaign,
                           check_end_conditions, next_hazard_id, phase_cleared)
from engine.rules import (RuleError, advance_turn, end_of_round,
                          hazard_attack, resolve_action)
from narrator.genesis import genesis_jobs
from storage.index_db import IndexDB
from storage.room_db import RoomDB, RoomNotFound

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no look-alike characters

# A table seats this many engineers, human or otherwise. There are more classes
# than seats on purpose: filling a room with bots should still leave choices.
MAX_SEATS = 6

#: The longest line anyone -- player or bot -- may put on the table.
CHAT_MAX = 500

#: How long the table will wait for the DM before it gives up and plays on. The
#: gate exists so the story cannot fall behind the game; the deadline exists so
#: a wedged model cannot freeze a table forever.
DEFAULT_DM_WAIT = 20.0


def dm_wait_seconds() -> float:
    """CP_DM_WAIT, in seconds. A malformed value falls back to the default
    rather than stopping the game from starting."""
    raw = os.environ.get("CP_DM_WAIT")
    if raw is None or raw.strip() == "":
        return DEFAULT_DM_WAIT
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_DM_WAIT
    return max(0.0, value)


#: Where deleted rooms go. Never listed, never indexed, never deleted by the app.
TRASH_DIR = "_trash"

_ROOM_CODE = re.compile(r"[A-Za-z0-9]{1,32}")


class ServiceError(Exception):
    """A request that is wrong about the world: unknown room, taken class, game over."""


class GameService:
    def __init__(self, root: str, catalog: Catalog, templates: dict,
                 archetypes: list, queue=None, broker=None) -> None:
        self.root = root
        self.catalog = catalog
        self.templates = templates
        self.archetypes = {a["id"]: a for a in archetypes}
        self.queue = queue
        self.broker = broker
        self.index = IndexDB(root)
        self._locks: dict = {}
        self._locks_guard = threading.Lock()
        self._rooms: dict = {}
        self._level_choices: dict = {}
        # Phase interludes staged under the room lock, submitted once it is
        # released; see _flush_phase_jobs.
        self._pending_phase: dict = {}
        # room_id -> {"event_seq": int, "deadline": monotonic}. While a room has
        # an entry here the table is waiting on the DM; see _gate_blocking.
        self._dm_gate: dict = {}

    # --- plumbing ----------------------------------------------------------

    def _lock(self, room_id: str) -> threading.Lock:
        with self._locks_guard:
            if room_id not in self._locks:
                self._locks[room_id] = threading.Lock()
            return self._locks[room_id]

    def _room(self, room_id: str) -> RoomDB:
        cached = self._rooms.get(room_id)
        if cached is not None and not cached.path.exists():
            # Deleted by an admin after a lock-free read re-cached it.
            self._rooms.pop(room_id, None)
            raise ServiceError(f"no such room: {room_id}")
        if room_id not in self._rooms:
            try:
                self._rooms[room_id] = RoomDB.open(self.root, room_id)
            except RoomNotFound as exc:
                raise ServiceError(f"no such room: {room_id}") from exc
        return self._rooms[room_id]

    def _dice(self, state: dict) -> Dice:
        """Advance the room seed each call so replays differ but stay reproducible."""
        seed = state["room"]["rng_seed"]
        state["room"]["rng_seed"] = (seed * 6364136223846793005 + 1) % (2 ** 63)
        return Dice(seed)

    def _reindex(self, room_id: str, state: dict) -> None:
        self.index.upsert(room_id, state["room"]["name"], state["room"]["archetype"],
                          state["room"]["phase_index"], state["room"]["status"],
                          len(state["characters"]),
                          sum(1 for c in state["characters"].values()
                              if c.get("is_bot")))

    def _publish(self, room_id: str, events: list) -> None:
        if self.broker is None:
            return
        for event in events:
            self.broker.publish(room_id, {"seq": event["seq"], "kind": event["kind"],
                                          **event["payload"]})

    # --- lobby -------------------------------------------------------------

    def create_room(self, name: str, archetype: str) -> str:
        if archetype not in self.archetypes:
            raise ServiceError(f"unknown archetype: {archetype}")
        while True:
            room_id = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            if not RoomDB.exists(self.root, room_id):
                break
        seed = secrets.randbelow(2 ** 31)
        room = RoomDB.create(self.root, room_id, name, archetype, seed)
        self._rooms[room_id] = room
        state = room.load_state()
        state["hazards"] = build_campaign(
            Dice(seed), self.templates,
            self.archetypes[archetype].get("subsystems"))
        state["active_hazard_id"] = next_hazard_id(state)
        room.save_state(state)
        room.append_event("room_created", None,
                          {"name": name, "archetype": archetype})
        self._reindex(room_id, state)
        self.enqueue_genesis(room_id)
        return room_id

    def list_rooms(self) -> list:
        return self.index.list_rooms()

    # --- admin -------------------------------------------------------------

    def room_size(self, room_id: str) -> int:
        """Bytes on disk for one room's directory (database, WAL, anything else)."""
        base = Path(self.root) / room_id
        total = 0
        for path in base.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                pass
        return total

    def delete_room(self, room_id: str) -> str:
        """Take a room out of play and move its directory into rooms/_trash.

        Nothing is unlinked: the folder lands in _trash/<code>-<UTC stamp>/ so a
        mistaken delete is a `mv` away from being undone. Returns that path.
        """
        room_id = str(room_id or "")
        # Only a plain room code may name a directory to move. RoomDB.exists
        # alone would accept "../x" or "_trash".
        if not _ROOM_CODE.fullmatch(room_id) or not RoomDB.exists(self.root, room_id):
            raise ServiceError(f"no such room: {room_id}")
        with self._lock(room_id):
            if not RoomDB.exists(self.root, room_id):   # lost a race to another delete
                raise ServiceError(f"no such room: {room_id}")
            if self.queue is not None and hasattr(self.queue, "drop_room"):
                self.queue.drop_room(room_id)
            room = self._rooms.pop(room_id, None)
            latest = 0
            if room is not None:
                try:
                    latest = room.latest_seq()
                    # Fold the WAL back in so the trashed game.db stands alone.
                    room.connect().execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                room.close()
            self._level_choices.pop(room_id, None)
            self._pending_phase.pop(room_id, None)
            self._dm_gate.pop(room_id, None)
            self.index.remove(room_id)
            if self.broker is not None:
                self.broker.publish(room_id, {
                    "seq": latest, "kind": "room_closed",
                    "message": "This room was closed by an admin."})
            trash = Path(self.root) / TRASH_DIR
            trash.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            target = trash / f"{room_id}-{stamp}"
            shutil.move(str(Path(self.root) / room_id), str(target))
            self._rooms.pop(room_id, None)   # a lock-free read may have re-cached it
            self._dm_gate.pop(room_id, None)
        with self._locks_guard:
            self._locks.pop(room_id, None)
        return str(target)

    def join_room(self, room_id: str, display_name: str, class_id: str,
                  appearance: "dict | None" = None) -> dict:
        if class_id not in self.catalog.classes:
            raise ServiceError(f"unknown class: {class_id}")
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            if any(c["class_id"] == class_id for c in state["characters"].values()):
                raise ServiceError(f"{class_id} is already taken in this room")
            player_id = "p" + secrets.token_hex(4)
            token = secrets.token_urlsafe(24)
            char, roll = new_character_detailed(player_id, display_name,
                                                self.catalog.classes[class_id],
                                                self.catalog, self._dice(state))
            # Appearance is presentation, not rules, so the pure engine never
            # sees it -- it is bolted on here and validated before it lands.
            char["appearance"] = (appearance_lib.normalise(appearance)
                                  if appearance
                                  else appearance_lib.random_appearance())
            start_seq = room.latest_seq()
            room.add_player(player_id, display_name, token, class_id)
            state["characters"][player_id] = char
            state["turn"]["order"].append(player_id)
            room.save_state(state)
            room.append_event("player_joined", player_id,
                              {"name": display_name, "class_id": class_id})
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(start_seq))
            # `roll` rides along on this one response and is deliberately not
            # written to the room state: it is a one-time reveal on the join
            # screen, and persisting it would mean another schema migration for
            # a blob nothing else ever reads.
            return {"player_id": player_id, "token": token, "character": char,
                    "roll": roll}

    # --- bots --------------------------------------------------------------

    def _bot_name(self, state: dict) -> str:
        """Unit-1, Unit-2, ... -- the lowest number this room is not using."""
        taken = {c["name"] for c in state["characters"].values()}
        number = 1
        while f"Unit-{number}" in taken:
            number += 1
        return f"Unit-{number}"

    def add_bot(self, room_id: str, class_id: str) -> dict:
        """Seat a computer-controlled engineer. Same seat rules as a human."""
        if class_id not in self.catalog.classes:
            raise ServiceError(f"unknown class: {class_id}")
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            if any(c["class_id"] == class_id for c in state["characters"].values()):
                raise ServiceError(f"{class_id} is already taken in this room")
            if len(state["characters"]) >= MAX_SEATS:
                raise ServiceError(f"this room is full ({MAX_SEATS} seats)")
            player_id = "b" + secrets.token_hex(4)
            name = self._bot_name(state)
            char, _ = new_character_detailed(player_id, name,
                                             self.catalog.classes[class_id],
                                             self.catalog, self._dice(state))
            char["appearance"] = appearance_lib.random_appearance()
            char["is_bot"] = True
            start_seq = room.latest_seq()
            # A bot still gets a players row -- characters is foreign-keyed to it
            # -- and a token nobody is ever handed, so no browser can drive it.
            room.add_player(player_id, name, secrets.token_urlsafe(24), class_id)
            state["characters"][player_id] = char
            state["turn"]["order"].append(player_id)
            room.save_state(state)
            room.append_event("bot_added", player_id,
                              {"name": name, "class_id": class_id})
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(start_seq))
            return {"player_id": player_id, "character": char}

    def remove_bot(self, room_id: str, player_id: str) -> dict:
        """Free a bot's seat. A human is never removable through this door."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            char = state["characters"].get(player_id)
            if char is None:
                raise ServiceError("no such engineer in this room")
            if not char.get("is_bot"):
                raise ServiceError("only a bot can be removed")
            start_seq = room.latest_seq()
            state["characters"].pop(player_id)
            order = state["turn"]["order"]
            if player_id in order:
                order.remove(player_id)
            # The removed seat may have been the active one, or below it.
            if order:
                state["turn"]["turn_index"] %= len(order)
            else:
                state["turn"]["turn_index"] = 0
            room.save_state(state)          # characters go first; the FK points here
            room.remove_player(player_id)
            room.append_event("bot_removed", player_id, {"name": char["name"]})
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(start_seq))
            return {"removed": player_id}

    def player_by_token(self, room_id: str, token: str) -> "dict | None":
        return self._room(room_id).player_by_token(token)

    def start_game(self, room_id: str) -> None:
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            if not state["characters"]:
                raise ServiceError("a room needs at least one player to start")
            start_seq = room.latest_seq()
            state["room"]["status"] = "active"
            state["active_hazard_id"] = next_hazard_id(state)
            room.save_state(state)
            room.append_event("game_started", None,
                              {"players": state["turn"]["order"]})
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(start_seq))

    # --- reads -------------------------------------------------------------

    def snapshot(self, room_id: str) -> dict:
        return self._room(room_id).load_state()

    def events_since(self, room_id: str, seq: int) -> list:
        return self._room(room_id).events_since(seq)

    def narration(self, room_id: str, event_seq: int) -> "dict | None":
        return self._room(room_id).narration(event_seq)

    def set_level_choice(self, room_id: str, player_id: str, stat: str) -> None:
        if stat not in STATS:
            raise ServiceError(f"unknown stat: {stat}")
        with self._lock(room_id):
            self._room(room_id)          # a deleted room must not collect choices
            self._level_choices.setdefault(room_id, {})[player_id] = stat

    # --- party chat ---------------------------------------------------------

    def subsystems(self, room_id: str) -> list:
        """The archetype's subsystem list for this room, or [] for an archetype
        written before they existed."""
        state = self.snapshot(room_id)
        archetype = self.archetypes.get(state["room"]["archetype"]) or {}
        return list(archetype.get("subsystems") or [])

    def post_chat(self, room_id: str, player_id: "str | None",
                  body: str) -> dict:
        """One line of table talk. The speaker's name comes from the room's own
        character, never from the request -- a body cannot forge a sender."""
        body = str(body or "").strip()
        if not body:
            raise ServiceError("a message needs some words")
        if len(body) > CHAT_MAX:
            raise ServiceError(f"a message is at most {CHAT_MAX} characters")
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            char = state["characters"].get(player_id) or {}
            name = char.get("name") or "Unknown"
            is_bot = bool(char.get("is_bot"))
            start_seq = room.latest_seq()
            message = room.add_message(player_id, name, body, is_bot)
            # The line goes in the event log as well as the messages table, so a
            # client that reconnects with ?since= replays the conversation in
            # the same stream as everything else that happened.
            room.append_event("chat", player_id, {
                "message_id": message["id"], "name": name, "body": body,
                "is_bot": is_bot})
            self._publish(room_id, room.events_since(start_seq))
        return message

    def chat_count(self, room_id: str) -> int:
        """How many lines this room has heard. The bots rotate their phrasings
        on it, so it has to come from the room's own durable history."""
        return self._room(room_id).message_count()

    def chat_since(self, room_id: str, since: int = 0) -> list:
        return self._room(room_id).messages_since(since)

    # --- the DM gate --------------------------------------------------------

    # The whole table waits for the DM. A client-side wait would let two
    # browsers disagree about whether the game is paused, so the gate lives
    # here, beside the lock that already serialises every other decision about
    # a room. Three things open it again: the narration lands, somebody skips,
    # or the deadline passes. Nothing ever holds the lock while waiting -- the
    # flag is set and the caller returns.

    def _gate_blocking(self, room_id: str, now: "float | None" = None) -> "dict | None":
        """The live gate for this room, or None. Expires a stale one in passing.

        Call with the room lock held: it mutates _dm_gate.
        """
        gate = self._dm_gate.get(room_id)
        if gate is None:
            return None
        now = time.monotonic() if now is None else now
        if now >= gate["deadline"]:
            self._dm_gate.pop(room_id, None)
            return None
        return gate

    def _require_dm_done(self, room_id: str) -> None:
        if self._gate_blocking(room_id) is not None:
            raise ServiceError("the DM is still writing")

    def dm_gate(self, room_id: str) -> dict:
        """What the table should be told: whether it is waiting, and on what."""
        with self._lock(room_id):
            gate = self._gate_blocking(room_id)
        if gate is None:
            return {"waiting": False, "event_seq": None}
        return {"waiting": True, "event_seq": gate["event_seq"]}

    def clear_dm_gate(self, room_id: str, event_seq: "int | None" = None) -> None:
        """Open the gate. Called by the narration worker on every path it can
        finish on -- model, template fallback, timeout -- because a gate that
        outlives a failed narration is a hung table.

        A stale clear (the deadline already fired and the *next* turn opened a
        new gate) must not open the new one, so the event_seq must match.
        """
        with self._lock(room_id):
            gate = self._dm_gate.get(room_id)
            if gate is None:
                return
            if event_seq is not None and gate["event_seq"] != event_seq:
                return
            self._dm_gate.pop(room_id, None)

    def skip_dm(self, room_id: str) -> dict:
        """Stop waiting, now, for everybody. The narration is not cancelled: it
        still arrives later and renders in its own place in the log."""
        with self._lock(room_id):
            room = self._room(room_id)
            gate = self._gate_blocking(room_id)
            event_seq = gate["event_seq"] if gate else None
            self._dm_gate.pop(room_id, None)
            start_seq = room.latest_seq()
            room.append_event("dm_skipped", None, {"event_seq": event_seq})
            self._publish(room_id, room.events_since(start_seq))
        return {"skipped": True, "event_seq": event_seq}

    # --- the turn ----------------------------------------------------------

    def _stat_choices(self, room_id: str, state: dict) -> dict:
        chosen = self._level_choices.get(room_id, {})
        out = {}
        for player_id, char in state["characters"].items():
            out[player_id] = chosen.get(
                player_id, self.catalog.classes[char["class_id"]].primary)
        return out

    def _after_turn(self, room_id: str, room: RoomDB, state: dict,
                    events: list) -> None:
        """Hazard progression, phase transitions, and end conditions."""
        if state["active_hazard_id"]:
            active = [h for h in state["hazards"]
                      if h["id"] == state["active_hazard_id"]]
            if active and active[0]["defeated"]:
                events.append(room.append_event(
                    "hazard_defeated", None, {"hazard_id": active[0]["id"],
                                              "name": active[0]["name"]}))
                state["active_hazard_id"] = next_hazard_id(state)

        if phase_cleared(state):
            summary = advance_phase(state, self.catalog,
                                    self._stat_choices(room_id, state))
            self._level_choices.pop(room_id, None)
            places.reset_phase(state)       # a new phase refills the coffee
            events.append(room.append_event("phase_advanced", None, summary))
            if not summary.get("won"):
                # Staged, not submitted: the narrator must never see a phase the
                # database has not committed yet, and the queue must never be
                # touched while the room lock is held. _flush_phase_jobs sends it
                # once save_state has returned and the lock is gone.
                self._pending_phase[room_id] = {
                    "kind": "phase", "priority": 1, "room_id": room_id,
                    "event_seq": None,
                    "phase": PHASES[state["room"]["phase_index"]][1],
                    "premise": state["room"].get("premise", ""),
                }

        ending = check_end_conditions(state)
        if ending == "win":
            state["room"]["status"] = "won"
        elif ending:
            state["room"]["status"] = ending.replace("lose_", "lost_")
        if ending:
            events.append(room.append_event("game_over", None,
                                            {"result": ending}))

    def _require_running(self, state: dict) -> None:
        status = state["room"]["status"]
        if status == "lobby":
            # Without this, the first player to join could solo the opening hazard
            # while everyone else is still choosing a class.
            raise ServiceError("the programme has not started yet")
        if status != "active":
            raise ServiceError("this game is over")

    def _close_turn(self, room: RoomDB, state: dict, event_seqs: list,
                    dice: "Dice | None" = None) -> None:
        """Pass the turn on, and if that finished the round, let the hazard
        answer. Shared by every way a turn can end -- an ability, a place
        action, or a pass -- so the three can never disagree about a round."""
        round_done = advance_turn(state)
        if not round_done:
            return
        attack = hazard_attack(state, dice if dice is not None else self._dice(state))
        expire_conditions(state)
        state["party"]["schedule"] -= 1
        event_seqs.append(room.append_event("hazard_attack", None, attack))
        # A gate boss that grows while it is still standing does so once
        # per round, here, and not on a fumble's extra attack.
        escalated = end_of_round(state)
        if escalated:
            event_seqs.append(room.append_event("hazard_rule", None, escalated))

    def act(self, room_id: str, player_id: str, ability_id: str,
            target_id: "str | None" = None) -> dict:
        ability = self.catalog.abilities.get(ability_id)
        if ability is None:
            raise ServiceError(f"unknown ability: {ability_id}")
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            self._require_dm_done(room_id)
            self._require_running(state)
            dice = self._dice(state)
            result = resolve_action(state, player_id, ability, dice, target_id)

            # The action event is broadcast to every subscriber, so it must not
            # carry a DC the party has not yet revealed.
            hazard = next((h for h in state["hazards"]
                           if h["id"] == state.get("active_hazard_id")), None)
            dc_public = result.dc if hazard and "dc" in hazard.get("revealed", []) else None

            action_seq = room.append_event("action", player_id, {
                "ability_id": ability.id, "ability_name": ability.name,
                "natural": result.natural, "stat": result.stat_used,
                "stat_mod": result.stat_mod, "roll_bonus": result.roll_bonus,
                "total": result.total, "dc": dc_public, "outcome": result.outcome,
                "rerolled": result.rerolled, "changes": result.changes,
            })
            room.create_narration(action_seq)
            event_seqs = [action_seq]
            self._close_turn(room, state, event_seqs, dice)

            self._after_turn(room_id, room, state, event_seqs)
            room.save_state(state)              # commit BEFORE queueing narration
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(action_seq - 1))
            if self.queue is not None:
                # This action queued a narration, so the table now owes it a
                # pause. With no queue nothing will ever arrive to open the
                # gate again, so no gate is set.
                self._dm_gate[room_id] = {
                    "event_seq": action_seq,
                    "deadline": time.monotonic() + dm_wait_seconds()}

        self._flush_phase_jobs(room_id)
        self._enqueue_narration(room_id, player_id, action_seq, state, ability, result)
        return {"event_seq": action_seq, "outcome": result.outcome,
                "natural": result.natural, "total": result.total, "dc": dc_public,
                "changes": result.changes,
                "events": self._room(room_id).events_since(action_seq - 1)}

    def end_turn(self, room_id: str, player_id: str) -> dict:
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            self._require_dm_done(room_id)
            self._require_running(state)
            if state["turn"]["order"][state["turn"]["turn_index"]] != player_id:
                raise RuleError("It is not your turn.")
            start_seq = room.latest_seq()
            room.append_event("passed", player_id, {})
            self._close_turn(room, state, [])
            self._after_turn(room_id, room, state, [])
            room.save_state(state)
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(start_seq))
            outcome = {"events": room.events_since(start_seq)}
        self._flush_phase_jobs(room_id)
        return outcome

    # --- the office ---------------------------------------------------------

    # Walking is free and positions are zones, not pixels: a client animates
    # the walk however it likes and tells the server only when it crosses into
    # a different zone. Place actions are the office's version of an ability --
    # same lock, same gate, same turn, same narration.

    def office_move(self, room_id: str, player_id: str, zone) -> dict:
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            char = state["characters"].get(player_id)
            if char is None:
                raise ServiceError("you are not seated in this room")
            if not places.is_zone(state, zone):
                raise ServiceError(f"no such place in the office: {zone}")
            before = places.zone_of(char)
            if before == zone:
                return {"zone": zone, "moved": False}
            char["office_zone"] = zone
            room.save_state(state)
            start_seq = room.latest_seq()
            room.append_event("office_move", player_id, {
                "player_id": player_id, "name": char["name"],
                "from": before, "zone": zone,
                "is_bot": bool(char.get("is_bot"))})
            self._publish(room_id, room.events_since(start_seq))
        return {"zone": zone, "moved": True}

    def office_act(self, room_id: str, player_id: str, action: str,
                   target_id: "str | None" = None) -> dict:
        if action not in places.PLACE_ACTIONS:
            raise ServiceError(f"unknown place action: {action}")
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            self._require_dm_done(room_id)
            self._require_running(state)
            char = state["characters"].get(player_id)
            zone = places.zone_of(char) if char else None
            changes = places.resolve_place(state, player_id, action, target_id)
            name = places.PLACE_ACTIONS[action]["name"]
            action_seq = room.append_event("place_action", player_id, {
                "action": action, "action_name": name, "zone": zone,
                "partner_id": places.desk_owner(zone),
                "outcome": "success", "changes": changes,
            })
            room.create_narration(action_seq)
            event_seqs = [action_seq]
            self._close_turn(room, state, event_seqs)
            self._after_turn(room_id, room, state, event_seqs)
            room.save_state(state)              # commit BEFORE queueing narration
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(action_seq - 1))
            if self.queue is not None:
                self._dm_gate[room_id] = {
                    "event_seq": action_seq,
                    "deadline": time.monotonic() + dm_wait_seconds()}

        self._flush_phase_jobs(room_id)
        self._enqueue_place_narration(room_id, player_id, action_seq, state,
                                      name, changes)
        return {"event_seq": action_seq, "action": action, "outcome": "success",
                "changes": changes,
                "events": self._room(room_id).events_since(action_seq - 1)}

    def set_desk(self, room_id: str, player_id: str, desk) -> dict:
        """Cosmetic, so it costs no turn and ignores the DM gate. Anything not
        on the option lists falls back to the default."""
        clean = places.normalise_desk(desk)
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            char = state["characters"].get(player_id)
            if char is None:
                raise ServiceError("you are not seated in this room")
            char["desk"] = clean
            room.save_state(state)
            start_seq = room.latest_seq()
            room.append_event("desk_updated", player_id,
                              {"player_id": player_id, "desk": clean})
            self._publish(room_id, room.events_since(start_seq))
        return {"desk": clean}

    def office(self, state: dict) -> dict:
        """The office block of /state: zones, desks, and who stands where."""
        return {
            "zones": places.zones(state),
            "fixed_zones": list(places.FIXED_ZONES),
            "desks": [{**d, "desk": places.normalise_desk(
                           state["characters"][d["player_id"]].get("desk"))}
                      for d in places.desk_assignments(state)],
            "positions": {pid: places.zone_of(c)
                          for pid, c in state["characters"].items()},
            "actions": {k: v["name"] for k, v in places.PLACE_ACTIONS.items()},
            "desk_options": places.DESK_OPTIONS,
            "coffee_limit": places.COFFEE_LIMIT,
        }

    # --- narration hand-off -------------------------------------------------

    def _flush_phase_jobs(self, room_id: str) -> None:
        """Submit a staged phase interlude. Call outside the room lock, after
        save_state -- same commit-before-narrate rule as _enqueue_narration."""
        job = self._pending_phase.pop(room_id, None)
        if job is not None and self.queue is not None:
            self.queue.submit(job)

    # --- the narrator's memory ----------------------------------------------

    # A 1B model infers no continuity. Each turn was narrated from a fact block
    # alone, so the prose read as disconnected vignettes. These two reads are
    # what turn it into a story: what was last said, and what has changed since.
    # Both come out of the room's own `events` table -- narration events are
    # already durable there, so there is no new schema and no second store that
    # could disagree with the log.

    HISTORY_PASSAGES = 2

    def _recent_narration(self, room_id: str) -> list:
        """The last few turn narrations for this room, oldest first."""
        room = self._room(room_id)
        # More rows than passages, because interludes and premises share the
        # kind and are filtered out here rather than in SQL.
        rows = room.recent_events("narration", self.HISTORY_PASSAGES * 3)
        texts = []
        for row in rows:
            payload = row.get("payload") or {}
            if payload.get("job_kind", "turn") != "turn":
                continue
            text = (payload.get("text") or "").strip()
            if text:
                texts.append(text)
            if len(texts) >= self.HISTORY_PASSAGES:
                break
        return list(reversed(texts))

    def _previous_actor(self, room_id: str, event_seq: int) -> "str | None":
        """Who acted on the turn before this one, if anyone did."""
        for row in self._room(room_id).recent_events("action", 4):
            if row["seq"] < event_seq:
                return row["actor"]
        return None

    def _enqueue_narration(self, room_id, player_id, event_seq, state, ability,
                           result) -> None:
        if self.queue is None:
            return
        hazard = next((h for h in state["hazards"]
                       if h["id"] == state.get("active_hazard_id")), None)
        char = state["characters"][player_id]
        remaining = None
        if hazard and hazard.get("max_severity"):
            remaining = max(0.0, hazard["severity"] / hazard["max_severity"])
        self.queue.submit({
            "kind": "turn", "priority": 0, "room_id": room_id,
            "event_seq": event_seq,
            "actor_name": char["name"],
            "actor_class": self.catalog.classes[char["class_id"]].name,
            "premise": state["room"].get("premise", ""),
            "phase": PHASES[state["room"]["phase_index"]][1],
            "hazard": hazard, "ability_name": ability.name,
            "outcome": result.outcome, "natural": result.natural,
            "total": result.total, "dc": result.dc, "changes": result.changes,
            "history": self._recent_narration(room_id),
            "severity_remaining": remaining,
            "same_engineer": self._previous_actor(room_id, event_seq) == player_id,
        })

    def _enqueue_place_narration(self, room_id, player_id, event_seq, state,
                                 action_name, changes) -> None:
        """A place action narrates like an ability that could not miss."""
        if self.queue is None:
            return
        hazard = next((h for h in state["hazards"]
                       if h["id"] == state.get("active_hazard_id")), None)
        char = state["characters"][player_id]
        remaining = None
        if hazard and hazard.get("max_severity"):
            remaining = max(0.0, hazard["severity"] / hazard["max_severity"])
        self.queue.submit({
            "kind": "turn", "priority": 0, "room_id": room_id,
            "event_seq": event_seq,
            "actor_name": char["name"],
            "actor_class": self.catalog.classes[char["class_id"]].name,
            "premise": state["room"].get("premise", ""),
            "phase": PHASES[state["room"]["phase_index"]][1],
            "hazard": hazard, "ability_name": action_name,
            "outcome": "success", "natural": None, "total": None, "dc": None,
            "changes": changes,
            "history": self._recent_narration(room_id),
            "severity_remaining": remaining,
            "same_engineer": self._previous_actor(room_id, event_seq) == player_id,
        })

    # --- test seams ---------------------------------------------------------

    def _set_party(self, room_id: str, **fields) -> None:
        """Force party resource values. Tests only; never called by the app."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            state["party"].update(fields)
            room.save_state(state)

    def _set_character(self, room_id: str, player_id: str, **fields) -> None:
        """Force character fields. Tests only; never called by the app."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            state["characters"][player_id].update(fields)
            room.save_state(state)

    def _force_defeat_active_hazard(self, room_id: str) -> None:
        """Mark the active hazard defeated and progress. Tests only."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            for hazard in state["hazards"]:
                if hazard["id"] == state["active_hazard_id"]:
                    hazard["severity"] = 0
                    hazard["defeated"] = True
            self._after_turn(room_id, room, state, [])
            room.save_state(state)
            self._reindex(room_id, state)
        self._flush_phase_jobs(room_id)

    def _force_clear_phase(self, room_id: str) -> None:
        """Defeat every hazard in the current phase. Tests only."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            for hazard in state["hazards"]:
                if hazard["phase_index"] == state["room"]["phase_index"]:
                    hazard["severity"] = 0
                    hazard["defeated"] = True
            self._after_turn(room_id, room, state, [])
            room.save_state(state)
            self._reindex(room_id, state)
        self._flush_phase_jobs(room_id)

    # --- genesis -----------------------------------------------------------

    def set_premise(self, room_id: str, text: str) -> None:
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            state["room"]["premise"] = text.strip()
            room.save_state(state)

    def rename_hazards(self, room_id: str, phase_index: int, entries: list) -> None:
        """Rewrite prose only. Severity, DC, attack type and weakness are untouched."""
        if not entries:
            return
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            targets = [h for h in state["hazards"]
                       if h["phase_index"] == phase_index
                       and not h["is_boss"] and not h["defeated"]]
            for hazard, (name, description) in zip(targets, entries):
                hazard["name"] = name
                hazard["description"] = description
            room.save_state(state)

    def enqueue_genesis(self, room_id: str) -> None:
        if self.queue is None:
            return
        state = self.snapshot(room_id)
        archetype = self.archetypes[state["room"]["archetype"]]
        for job in genesis_jobs(room_id, state, archetype):
            self.queue.submit(job)
