"""The two migrations the map and the chat needed, and the messages table.

There are real room databases on disk that predate both, so each is exercised
against a database hand-built the way an earlier release left it -- same shape
of test as the appearance and is_bot migrations before them.
"""
import pathlib
import sqlite3

import pytest
from storage.room_db import RoomDB
from tests.engine.test_effects import make_state


@pytest.fixture
def room(tmp_path):
    db = RoomDB.create(str(tmp_path), "abc123", "Kestrel", "aircraft", 4242)
    yield db
    db.close()


def _pre_map_schema() -> str:
    """schema.sql as it read before hazards gained `subsystem` and before the
    messages table existed at all."""
    schema = (pathlib.Path("storage") / "schema.sql").read_text()
    schema = schema.replace(
        "    is_boss       INTEGER NOT NULL DEFAULT 0,\n"
        "    subsystem     TEXT NOT NULL DEFAULT ''\n",
        "    is_boss       INTEGER NOT NULL DEFAULT 0\n")
    start = schema.index("CREATE TABLE IF NOT EXISTS messages")
    end = schema.index(");", start) + 3
    return schema[:start] + schema[end:]


def _make_old_room(tmp_path, room_id="old456"):
    path = tmp_path / room_id / "game.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_pre_map_schema())
    now = 1700000000.0
    conn.execute(
        "INSERT INTO room (id, name, premise, archetype, phase_index, status,"
        " rng_seed, created_at, last_active) VALUES (?,?,?,?,0,'lobby',?,?,?)",
        (room_id, "Kestrel", "p", "aircraft", 4242, now, now))
    conn.execute("INSERT INTO party (id) VALUES (1)")
    conn.execute("INSERT INTO turn_state (id) VALUES (1)")
    conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(hazards)")}
    assert "subsystem" not in columns
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "messages" not in tables
    conn.close()
    return path


# --- the subsystem column ---------------------------------------------------

def test_a_database_without_the_subsystem_column_gains_it(tmp_path):
    _make_old_room(tmp_path)
    db = RoomDB.open(str(tmp_path), "old456")
    columns = {row[1] for row in
               db.connect().execute("PRAGMA table_info(hazards)")}
    assert "subsystem" in columns
    db.close()


def test_a_migrated_database_round_trips_a_placed_hazard(tmp_path):
    _make_old_room(tmp_path)
    db = RoomDB.open(str(tmp_path), "old456")
    fresh = make_state()
    fresh["room"].update({"id": "old456", "name": "Kestrel",
                          "archetype": "aircraft", "rng_seed": 4242,
                          "premise": "p"})
    fresh["hazards"][0]["subsystem"] = "avionics"
    for player_id, char in fresh["characters"].items():
        db.add_player(player_id, char["name"], f"tok-{player_id}",
                      char["class_id"])
    db.save_state(fresh)
    assert db.load_state()["hazards"][0]["subsystem"] == "avionics"
    db.close()


def test_an_unplaced_hazard_carries_no_subsystem_key(room):
    """Same rule as appearance and is_bot: what went in comes back out."""
    state = make_state()
    state["room"].update({"id": "abc123", "name": "Kestrel",
                          "archetype": "aircraft", "rng_seed": 4242})
    for player_id, char in state["characters"].items():
        room.add_player(player_id, char["name"], f"tok-{player_id}",
                        char["class_id"])
    room.save_state(state)
    assert "subsystem" not in room.load_state()["hazards"][0]


# --- the messages table -----------------------------------------------------

def test_a_database_without_the_messages_table_gains_it(tmp_path):
    _make_old_room(tmp_path)
    db = RoomDB.open(str(tmp_path), "old456")
    tables = {row[0] for row in db.connect().execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "messages" in tables
    db.close()


def test_the_new_migrations_are_idempotent(tmp_path):
    _make_old_room(tmp_path)
    for _ in range(3):
        db = RoomDB.open(str(tmp_path), "old456")
        db.connect()
        db.close()
    db = RoomDB.open(str(tmp_path), "old456")
    columns = [row[1] for row in
               db.connect().execute("PRAGMA table_info(hazards)")]
    assert columns.count("subsystem") == 1
    db.add_message("p1", "Ada", "still here", False)
    assert db.message_count() == 1
    db.close()


def test_a_message_round_trips(room):
    written = room.add_message("p1", "Ada", "the pack runs hot", False)
    [read] = room.messages_since(0)
    assert read["name"] == "Ada"
    assert read["body"] == "the pack runs hot"
    assert read["is_bot"] is False
    assert read["id"] == written["id"]


def test_a_bot_message_is_marked_as_one(room):
    room.add_message("b1", "Unit-1", "scoping this", True)
    assert room.messages_since(0)[0]["is_bot"] is True


def test_messages_since_returns_only_newer_ones(room):
    first = room.add_message("p1", "Ada", "one", False)
    room.add_message("p1", "Ada", "two", False)
    assert [m["body"] for m in room.messages_since(first["id"])] == ["two"]


def test_only_the_last_two_hundred_are_kept(room):
    for index in range(RoomDB.MESSAGE_LIMIT + 25):
        room.add_message("p1", "Ada", f"line {index}", False)
    assert room.message_count() == RoomDB.MESSAGE_LIMIT
    kept = room.messages_since(0)
    assert kept[0]["body"] == "line 25"
    assert kept[-1]["body"] == f"line {RoomDB.MESSAGE_LIMIT + 24}"
