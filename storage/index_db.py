"""Lobby registry. A rebuildable cache over rooms/*/game.db, never authoritative."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from storage.room_db import RoomDB

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS rooms (
    room_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    archetype    TEXT NOT NULL,
    phase_index  INTEGER NOT NULL,
    status       TEXT NOT NULL,
    player_count INTEGER NOT NULL,
    last_active  REAL NOT NULL
);
"""


class IndexDB:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "index.db"
        self._local = threading.local()
        self.connect().executescript(_DDL)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def list_rooms(self) -> list:
        return [dict(r) for r in self.connect().execute(
            "SELECT * FROM rooms ORDER BY last_active DESC")]

    def upsert(self, room_id: str, name: str, archetype: str, phase_index: int,
               status: str, player_count: int) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT INTO rooms (room_id, name, archetype, phase_index, status,"
                " player_count, last_active) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(room_id) DO UPDATE SET name=excluded.name,"
                " archetype=excluded.archetype, phase_index=excluded.phase_index,"
                " status=excluded.status, player_count=excluded.player_count,"
                " last_active=excluded.last_active",
                (room_id, name, archetype, phase_index, status, player_count,
                 time.time()))

    def remove(self, room_id: str) -> None:
        conn = self.connect()
        with conn:
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))

    def rebuild(self) -> int:
        """Reconstruct the whole index by scanning the room databases."""
        conn = self.connect()
        with conn:
            conn.execute("DELETE FROM rooms")
        count = 0
        for room_id in RoomDB.list_room_ids(str(self.root)):
            try:
                room = RoomDB.open(str(self.root), room_id)
            except Exception:
                log.warning("index rebuild: cannot open room %s; skipping", room_id)
                continue
            try:
                state = room.load_state()
                self.upsert(room_id, state["room"]["name"],
                            state["room"]["archetype"],
                            state["room"]["phase_index"], state["room"]["status"],
                            len(room.players()))
                count += 1
            except Exception:
                log.warning("index rebuild: room %s is unreadable; skipping", room_id)
            finally:
                room.close()
        return count
