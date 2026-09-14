# storage/room_db.py
"""One SQLite database per room. The source of truth for a single game."""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class RoomNotFound(Exception):
    """Raised when a room directory or database is absent."""


class RoomDB:
    """Connections are per-thread; Flask's threaded server needs that."""

    def __init__(self, db_path: Path, room_id: str) -> None:
        self.path = Path(db_path)
        self.room_id = room_id
        self._local = threading.local()

    # --- lifecycle ---------------------------------------------------------

    @staticmethod
    def _db_path(root: str, room_id: str) -> Path:
        return Path(root) / room_id / "game.db"

    @classmethod
    def exists(cls, root: str, room_id: str) -> bool:
        return cls._db_path(root, room_id).exists()

    @classmethod
    def list_room_ids(cls, root: str) -> list:
        base = Path(root)
        if not base.exists():
            return []
        return [p.name for p in base.iterdir()
                if p.is_dir() and (p / "game.db").exists()]

    @classmethod
    def create(cls, root: str, room_id: str, name: str, archetype: str,
               rng_seed: int) -> "RoomDB":
        path = cls._db_path(root, room_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = cls(path, room_id)
        conn = db.connect()
        conn.executescript(_SCHEMA)
        now = time.time()
        conn.execute(
            "INSERT OR REPLACE INTO room "
            "(id, name, premise, archetype, phase_index, status, rng_seed,"
            " created_at, last_active) VALUES (?,?,?,?,0,'lobby',?,?,?)",
            (room_id, name, "", archetype, rng_seed, now, now))
        conn.execute("INSERT OR IGNORE INTO party (id) VALUES (1)")
        conn.execute("INSERT OR IGNORE INTO turn_state (id) VALUES (1)")
        conn.commit()
        return db

    @classmethod
    def open(cls, root: str, room_id: str) -> "RoomDB":
        path = cls._db_path(root, room_id)
        if not path.exists():
            raise RoomNotFound(room_id)
        return cls(path, room_id)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._migrate(conn)
            self._local.conn = conn
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring an older room database up to the current schema.

        schema.sql is all CREATE TABLE IF NOT EXISTS, so a database written
        before a column existed never gains it -- the script is a no-op on a
        table that is already there. Rooms outlive releases, so each added
        column needs an idempotent ALTER here or save_state fails on every
        game created before the change.
        """
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "characters" not in tables:
            return                      # a brand-new file; schema.sql runs next
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(characters)")}
        if "appearance" not in columns:
            conn.execute("ALTER TABLE characters"
                         " ADD COLUMN appearance TEXT NOT NULL DEFAULT '{}'")
            conn.commit()
        if "is_bot" not in columns:
            conn.execute("ALTER TABLE characters"
                         " ADD COLUMN is_bot INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        hazard_columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(hazards)")}
        if hazard_columns and "subsystem" not in hazard_columns:
            conn.execute("ALTER TABLE hazards"
                         " ADD COLUMN subsystem TEXT NOT NULL DEFAULT ''")
            conn.commit()
        # A whole new table, not a column: CREATE TABLE IF NOT EXISTS is already
        # idempotent, but schema.sql only ever runs on create, so an existing
        # room would never see it without this line.
        if "messages" not in tables:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts REAL NOT NULL, player_id TEXT, name TEXT NOT NULL,"
                " body TEXT NOT NULL, is_bot INTEGER NOT NULL DEFAULT 0)")
            conn.commit()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # --- state -------------------------------------------------------------

    @staticmethod
    @contextlib.contextmanager
    def _snapshot(conn: sqlite3.Connection):
        """One WAL read snapshot for every SELECT inside the block.

        Without it each SELECT takes its own snapshot, so a read that
        interleaves with another player's write can return `characters` from
        after the commit and `turn_state` from before it -- the UI then briefly
        enables ability buttons for the wrong player. BEGIN DEFERRED takes no
        lock and blocks no writer; it only pins what this reader sees.
        """
        if conn.in_transaction:             # already inside one; do not nest
            yield
            return
        conn.execute("BEGIN DEFERRED")
        try:
            yield
        finally:
            # Read-only either way: COMMIT on a deferred read-only transaction
            # releases the snapshot and writes nothing.
            conn.execute("COMMIT")

    def load_state(self) -> dict:
        conn = self.connect()
        with self._snapshot(conn):
            return self._read_state(conn)

    def _read_state(self, conn: sqlite3.Connection) -> dict:
        room = dict(conn.execute("SELECT * FROM room WHERE id = ?",
                                 (self.room_id,)).fetchone())
        party = dict(conn.execute("SELECT * FROM party WHERE id = 1").fetchone())
        turn = dict(conn.execute("SELECT * FROM turn_state WHERE id = 1").fetchone())

        characters = {}
        for row in conn.execute("SELECT * FROM characters"):
            row = dict(row)
            characters[row["player_id"]] = {
                "player_id": row["player_id"], "name": row["name"],
                "class_id": row["class_id"], "stats": json.loads(row["stats"]),
                "level": row["level"], "stamina": row["stamina"],
                "max_stamina": row["max_stamina"], "focus": row["focus"],
                "max_focus": row["max_focus"],
                "unlocked": json.loads(row["unlocked"]),
                "used": json.loads(row["used"]),
            }
            # An empty appearance is left off entirely rather than stored as {}:
            # a state that went in without the key comes back out the same shape.
            appearance = json.loads(row["appearance"] or "{}")
            if appearance:
                characters[row["player_id"]]["appearance"] = appearance
            # Same rule as appearance: a human carries no is_bot key at all, so
            # a state that went in without one comes back out the same shape.
            if row["is_bot"]:
                characters[row["player_id"]]["is_bot"] = True

        hazards = []
        for row in conn.execute(
                "SELECT * FROM hazards ORDER BY phase_index, ordinal"):
            row = dict(row)
            row["revealed"] = json.loads(row["revealed"])
            row["defeated"] = bool(row["defeated"])
            row["is_boss"] = bool(row["is_boss"])
            # Same rule as appearance and is_bot: an unplaced hazard carries no
            # subsystem key at all, so a state that went in without one -- an
            # old room, or a campaign built with no archetype list -- comes back
            # out the same shape.
            if not row["subsystem"]:
                row.pop("subsystem")
            hazards.append(row)

        state = {
            "room": {"id": room["id"], "name": room["name"],
                     "premise": room["premise"], "archetype": room["archetype"],
                     "phase_index": room["phase_index"], "status": room["status"],
                     "rng_seed": room["rng_seed"]},
            "party": {"budget": party["budget"], "schedule": party["schedule"],
                      "tech_debt": party["tech_debt"]},
            "characters": characters,
            "hazards": hazards,
            "active_hazard_id": turn["active_hazard_id"],
            "turn": {"round": turn["round"], "turn_index": turn["turn_index"],
                     "order": json.loads(turn["turn_order"])},
            "conditions": json.loads(turn["conditions"]),
        }
        if turn["last_ability_id"]:
            state["last_ability_id"] = turn["last_ability_id"]
        return state

    def save_state(self, state: dict) -> None:
        conn = self.connect()
        room, party = state["room"], state["party"]
        with conn:
            conn.execute(
                "UPDATE room SET name=?, premise=?, archetype=?, phase_index=?,"
                " status=?, rng_seed=?, last_active=? WHERE id=?",
                (room["name"], room.get("premise", ""), room["archetype"],
                 room["phase_index"], room["status"], room["rng_seed"],
                 time.time(), self.room_id))
            conn.execute(
                "UPDATE party SET budget=?, schedule=?, tech_debt=? WHERE id=1",
                (party["budget"], party["schedule"], party["tech_debt"]))
            conn.execute(
                "UPDATE turn_state SET round=?, turn_index=?, turn_order=?,"
                " active_hazard_id=?, conditions=?, last_ability_id=? WHERE id=1",
                (state["turn"]["round"], state["turn"]["turn_index"],
                 json.dumps(state["turn"]["order"]), state.get("active_hazard_id"),
                 json.dumps(state.get("conditions", [])),
                 state.get("last_ability_id")))

            conn.execute("DELETE FROM characters")
            for char in state["characters"].values():
                conn.execute(
                    "INSERT INTO characters (player_id, name, class_id, stats, level,"
                    " stamina, max_stamina, focus, max_focus, unlocked, used,"
                    " appearance, is_bot) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (char["player_id"], char["name"], char["class_id"],
                     json.dumps(char["stats"]), char["level"], char["stamina"],
                     char["max_stamina"], char["focus"], char["max_focus"],
                     json.dumps(char["unlocked"]), json.dumps(char["used"]),
                     json.dumps(char.get("appearance") or {}),
                     int(bool(char.get("is_bot", False)))))

            conn.execute("DELETE FROM hazards")
            for hazard in state["hazards"]:
                conn.execute(
                    "INSERT INTO hazards (id, phase_index, ordinal, name, description,"
                    " severity, max_severity, dc, attack_type, weakness, revealed,"
                    " defeated, is_boss, subsystem) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (hazard["id"], hazard["phase_index"], hazard["ordinal"],
                     hazard["name"], hazard["description"], hazard["severity"],
                     hazard["max_severity"], hazard["dc"], hazard["attack_type"],
                     hazard["weakness"], json.dumps(hazard["revealed"]),
                     int(hazard["defeated"]), int(hazard["is_boss"]),
                     hazard.get("subsystem") or ""))

    # --- players -----------------------------------------------------------

    def add_player(self, player_id: str, name: str, token: str,
                   class_id: str) -> None:
        conn = self.connect()
        seat = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        now = time.time()
        with conn:
            conn.execute(
                "INSERT INTO players (player_id, display_name, token, class_id,"
                " joined_at, last_seen, seat) VALUES (?,?,?,?,?,?,?)",
                (player_id, name, token, class_id, now, now, seat))

    def remove_player(self, player_id: str) -> None:
        conn = self.connect()
        with conn:
            conn.execute("DELETE FROM players WHERE player_id = ?", (player_id,))

    def player_by_token(self, token: str) -> "dict | None":
        row = self.connect().execute(
            "SELECT * FROM players WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None

    def players(self) -> list:
        return [dict(r) for r in self.connect().execute(
            "SELECT * FROM players ORDER BY seat")]

    # --- events and narration ---------------------------------------------

    def append_event(self, kind: str, actor: "str | None", payload: dict) -> int:
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO events (ts, kind, actor, payload) VALUES (?,?,?,?)",
                (time.time(), kind, actor, json.dumps(payload)))
        return cur.lastrowid

    def events_since(self, seq: int, limit: int = 500) -> list:
        rows = self.connect().execute(
            "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
            (seq, limit))
        out = []
        for row in rows:
            row = dict(row)
            row["payload"] = json.loads(row["payload"])
            out.append(row)
        return out

    def recent_events(self, kind: str, limit: int = 5) -> list:
        """The most recent events of one kind, newest first.

        The narrator's memory is read through here: `narration` events are
        already durable in this table, so remembering the story needs no new
        schema and no second store that could disagree with the log.
        """
        rows = self.connect().execute(
            "SELECT * FROM events WHERE kind = ? ORDER BY seq DESC LIMIT ?",
            (kind, limit))
        out = []
        for row in rows:
            row = dict(row)
            row["payload"] = json.loads(row["payload"])
            out.append(row)
        return out

    def latest_seq(self) -> int:
        row = self.connect().execute("SELECT MAX(seq) FROM events").fetchone()
        return row[0] or 0

    # --- party chat --------------------------------------------------------

    #: How much of the conversation a room keeps. Older lines are trimmed on
    #: insert rather than by a sweep, so the table never grows unbounded and
    #: there is no second job to forget to run.
    MESSAGE_LIMIT = 200

    @staticmethod
    def _message_row(row) -> dict:
        row = dict(row)
        row["is_bot"] = bool(row["is_bot"])
        return row

    def add_message(self, player_id: "str | None", name: str, body: str,
                    is_bot: bool = False) -> dict:
        conn = self.connect()
        now = time.time()
        with conn:
            cur = conn.execute(
                "INSERT INTO messages (ts, player_id, name, body, is_bot)"
                " VALUES (?,?,?,?,?)",
                (now, player_id, name, body, int(bool(is_bot))))
            message_id = cur.lastrowid
            conn.execute("DELETE FROM messages WHERE id <= ?",
                         (message_id - self.MESSAGE_LIMIT,))
        return {"id": message_id, "ts": now, "player_id": player_id,
                "name": name, "body": body, "is_bot": bool(is_bot)}

    def messages_since(self, since: int = 0,
                       limit: "int | None" = None) -> list:
        limit = self.MESSAGE_LIMIT if limit is None else limit
        rows = self.connect().execute(
            "SELECT * FROM messages WHERE id > ? ORDER BY id LIMIT ?",
            (since, limit))
        return [self._message_row(row) for row in rows]

    def message_count(self) -> int:
        return self.connect().execute(
            "SELECT COUNT(*) FROM messages").fetchone()[0]

    def create_narration(self, event_seq: int) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO narrations (event_seq, status, text, source)"
                " VALUES (?, 'pending', '', '')", (event_seq,))

    def update_narration(self, event_seq: int, status: str, text: str,
                         source: str) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT INTO narrations (event_seq, status, text, source)"
                " VALUES (?,?,?,?) ON CONFLICT(event_seq) DO UPDATE SET"
                " status=excluded.status, text=excluded.text, source=excluded.source",
                (event_seq, status, text, source))

    def narration(self, event_seq: int) -> "dict | None":
        row = self.connect().execute(
            "SELECT * FROM narrations WHERE event_seq = ?", (event_seq,)).fetchone()
        return dict(row) if row else None
