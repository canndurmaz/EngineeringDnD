"""Orchestration: per-room locks, and the seam between pure engine and storage."""
from __future__ import annotations

import secrets
import threading

import appearance as appearance_lib
from engine.character import new_character_detailed
from engine.classes import STATS, Catalog
from engine.dice import Dice
from engine.effects import expire_conditions
from engine.phases import (PHASES, advance_phase, build_campaign,
                           check_end_conditions, next_hazard_id, phase_cleared)
from engine.rules import RuleError, advance_turn, hazard_attack, resolve_action
from narrator.genesis import genesis_jobs
from storage.index_db import IndexDB
from storage.room_db import RoomDB, RoomNotFound

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no look-alike characters

# A table seats this many engineers, human or otherwise. There are more classes
# than seats on purpose: filling a room with bots should still leave choices.
MAX_SEATS = 6


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

    # --- plumbing ----------------------------------------------------------

    def _lock(self, room_id: str) -> threading.Lock:
        with self._locks_guard:
            if room_id not in self._locks:
                self._locks[room_id] = threading.Lock()
            return self._locks[room_id]

    def _room(self, room_id: str) -> RoomDB:
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
        state["hazards"] = build_campaign(Dice(seed), self.templates)
        state["active_hazard_id"] = next_hazard_id(state)
        room.save_state(state)
        room.append_event("room_created", None,
                          {"name": name, "archetype": archetype})
        self._reindex(room_id, state)
        self.enqueue_genesis(room_id)
        return room_id

    def list_rooms(self) -> list:
        return self.index.list_rooms()

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
        self._level_choices.setdefault(room_id, {})[player_id] = stat

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

    def act(self, room_id: str, player_id: str, ability_id: str,
            target_id: "str | None" = None) -> dict:
        ability = self.catalog.abilities.get(ability_id)
        if ability is None:
            raise ServiceError(f"unknown ability: {ability_id}")
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
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

            round_done = advance_turn(state)
            if round_done:
                attack = hazard_attack(state, dice)
                expire_conditions(state)
                state["party"]["schedule"] -= 1
                event_seqs.append(room.append_event("hazard_attack", None, attack))

            self._after_turn(room_id, room, state, event_seqs)
            room.save_state(state)              # commit BEFORE queueing narration
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(action_seq - 1))

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
            self._require_running(state)
            if state["turn"]["order"][state["turn"]["turn_index"]] != player_id:
                raise RuleError("It is not your turn.")
            start_seq = room.latest_seq()
            room.append_event("passed", player_id, {})
            round_done = advance_turn(state)
            if round_done:
                attack = hazard_attack(state, self._dice(state))
                expire_conditions(state)
                state["party"]["schedule"] -= 1
                room.append_event("hazard_attack", None, attack)
            self._after_turn(room_id, room, state, [])
            room.save_state(state)
            self._reindex(room_id, state)
            self._publish(room_id, room.events_since(start_seq))
            outcome = {"events": room.events_since(start_seq)}
        self._flush_phase_jobs(room_id)
        return outcome

    # --- narration hand-off -------------------------------------------------

    def _flush_phase_jobs(self, room_id: str) -> None:
        """Submit a staged phase interlude. Call outside the room lock, after
        save_state -- same commit-before-narrate rule as _enqueue_narration."""
        job = self._pending_phase.pop(room_id, None)
        if job is not None and self.queue is not None:
            self.queue.submit(job)

    def _enqueue_narration(self, room_id, player_id, event_seq, state, ability,
                           result) -> None:
        if self.queue is None:
            return
        hazard = next((h for h in state["hazards"]
                       if h["id"] == state.get("active_hazard_id")), None)
        char = state["characters"][player_id]
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
        })

    # --- test seams ---------------------------------------------------------

    def _set_party(self, room_id: str, **fields) -> None:
        """Force party resource values. Tests only; never called by the app."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            state["party"].update(fields)
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
