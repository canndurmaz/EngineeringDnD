"""The `rule` column on hazards, and the migration that adds it.

Same shape of test as the `subsystem` migration before it: there are room
databases on disk written before the column existed, and every one of them has
to keep saving.
"""
import pathlib
import sqlite3

import pytest
from storage.room_db import RoomDB
from tests.engine.test_effects import make_state

RULE = {"id": "focused_fire", "text": "Chip damage bounces off.",
        "params": {"threshold": 8, "floor": 1}}


@pytest.fixture
def room(tmp_path):
    db = RoomDB.create(str(tmp_path), "abc123", "Kestrel", "aircraft", 4242)
    yield db
    db.close()


def _pre_rule_schema() -> str:
    """schema.sql as it read before hazards gained `rule`."""
    schema = (pathlib.Path("storage") / "schema.sql").read_text()
    return schema.replace(
        "    subsystem     TEXT NOT NULL DEFAULT '',\n"
        "    rule          TEXT NOT NULL DEFAULT ''\n",
        "    subsystem     TEXT NOT NULL DEFAULT ''\n")


def _make_old_room(tmp_path, room_id="old789"):
    path = tmp_path / room_id / "game.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_pre_rule_schema())
    now = 1700000000.0
    conn.execute(
        "INSERT INTO room (id, name, premise, archetype, phase_index, status,"
        " rng_seed, created_at, last_active) VALUES (?,?,?,?,0,'lobby',?,?,?)",
        (room_id, "Kestrel", "p", "aircraft", 4242, now, now))
    conn.execute("INSERT INTO party (id) VALUES (1)")
    conn.execute("INSERT INTO turn_state (id) VALUES (1)")
    conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(hazards)")}
    assert "rule" not in columns
    conn.close()
    return path


def _seed(db, state, room_id):
    state["room"].update({"id": room_id, "name": "Kestrel", "premise": "p",
                          "archetype": "aircraft", "rng_seed": 4242})
    for player_id, char in state["characters"].items():
        db.add_player(player_id, char["name"], f"tok-{player_id}",
                      char["class_id"])
    return state


def test_a_database_without_the_rule_column_gains_it(tmp_path):
    _make_old_room(tmp_path)
    db = RoomDB.open(str(tmp_path), "old789")
    columns = {row[1] for row in
               db.connect().execute("PRAGMA table_info(hazards)")}
    assert "rule" in columns
    db.close()


def test_a_migrated_database_round_trips_a_boss_rule(tmp_path):
    _make_old_room(tmp_path)
    db = RoomDB.open(str(tmp_path), "old789")
    state = _seed(db, make_state(), "old789")
    state["hazards"][0]["rule"] = RULE
    db.save_state(state)
    assert db.load_state()["hazards"][0]["rule"] == RULE
    db.close()


def test_the_rule_migration_is_idempotent(tmp_path):
    _make_old_room(tmp_path)
    for _ in range(3):
        db = RoomDB.open(str(tmp_path), "old789")
        db.connect()
        db.close()
    db = RoomDB.open(str(tmp_path), "old789")
    columns = [row[1] for row in
               db.connect().execute("PRAGMA table_info(hazards)")]
    assert columns.count("rule") == 1
    db.close()


def test_an_ordinary_problem_carries_no_rule_key(room):
    """Same rule as subsystem: what went in comes back out the same shape."""
    room.save_state(_seed(room, make_state(), "abc123"))
    assert "rule" not in room.load_state()["hazards"][0]
