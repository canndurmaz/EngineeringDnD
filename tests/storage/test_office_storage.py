"""The office columns: office_zone, desk, coffee_used.

Real rooms on disk predate them, so the migration is exercised against a
database hand-built without them -- same shape as the is_bot migration tests.
"""
import pathlib
import sqlite3

from storage.room_db import RoomDB
from tests.engine.test_effects import make_state

OFFICE_COLUMNS = ("office_zone", "desk", "coffee_used")


def _pre_office_schema() -> str:
    schema = (pathlib.Path("storage") / "schema.sql").read_text()
    old = ("    is_bot       INTEGER NOT NULL DEFAULT 0,\n"
           "    office_zone  TEXT NOT NULL DEFAULT 'floor',\n"
           "    desk         TEXT NOT NULL DEFAULT '{}',\n"
           "    coffee_used  INTEGER NOT NULL DEFAULT 0\n")
    assert old in schema
    return schema.replace(old, "    is_bot       INTEGER NOT NULL DEFAULT 0\n")


def _make_pre_office_room(tmp_path, room_id="off123", with_player=True):
    path = tmp_path / room_id / "game.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_pre_office_schema())
    now = 1700000000.0
    conn.execute(
        "INSERT INTO room (id, name, premise, archetype, phase_index, status,"
        " rng_seed, created_at, last_active) VALUES (?,?,?,?,0,'active',?,?,?)",
        (room_id, "Kestrel", "p", "aircraft", 4242, now, now))
    conn.execute("INSERT INTO party (id) VALUES (1)")
    conn.execute("INSERT INTO turn_state (id, turn_order) VALUES (1, '[\"p1\"]')")
    if with_player:
        conn.execute(
            "INSERT INTO players (player_id, display_name, token, class_id,"
            " joined_at, last_seen, seat) VALUES ('p1','Ada','t','computer_scientist',?,?,0)",
            (now, now))
        conn.execute(
            "INSERT INTO characters (player_id, name, class_id, stats, level,"
            " stamina, max_stamina, focus, max_focus, unlocked, used, appearance,"
            " is_bot) VALUES ('p1','Ada','computer_scientist','{}',1,10,10,3,5,"
            "'[]','{}','{}',0)")
    conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(characters)")}
    assert not columns & set(OFFICE_COLUMNS)
    conn.close()
    return path


def _columns(db):
    return {row[1] for row in db.connect().execute("PRAGMA table_info(characters)")}


def test_a_pre_office_database_gains_all_three_columns(tmp_path):
    _make_pre_office_room(tmp_path)
    db = RoomDB.open(str(tmp_path), "off123")
    assert set(OFFICE_COLUMNS) <= _columns(db)
    db.close()


def test_existing_characters_get_the_defaults(tmp_path):
    _make_pre_office_room(tmp_path)
    db = RoomDB.open(str(tmp_path), "off123")
    row = dict(db.connect().execute(
        "SELECT office_zone, desk, coffee_used FROM characters").fetchone())
    assert row == {"office_zone": "floor", "desk": "{}", "coffee_used": 0}
    char = db.load_state()["characters"]["p1"]
    assert not set(OFFICE_COLUMNS) & set(char)
    db.close()


def test_the_office_migration_is_idempotent(tmp_path):
    _make_pre_office_room(tmp_path)
    for _ in range(3):
        db = RoomDB.open(str(tmp_path), "off123")
        assert set(OFFICE_COLUMNS) <= _columns(db)
        db.close()


def test_a_migrated_database_round_trips_the_office(tmp_path):
    _make_pre_office_room(tmp_path, with_player=False)
    db = RoomDB.open(str(tmp_path), "off123")
    fresh = make_state()
    fresh["room"].update({"id": "off123", "name": "Kestrel",
                          "archetype": "aircraft", "rng_seed": 4242,
                          "premise": "p"})
    fresh["characters"]["p1"].update({
        "office_zone": "desk:p2", "coffee_used": 2,
        "desk": {"desk_color": "teal", "monitor": "dual", "plant": "fern",
                 "mug": "tea", "poster": "gantt"}})
    for player_id, char in fresh["characters"].items():
        db.add_player(player_id, char["name"], f"tok-{player_id}",
                      char["class_id"])
    db.save_state(fresh)
    loaded = db.load_state()
    assert loaded == fresh
    # Defaults are left off, same rule as appearance and is_bot.
    assert not set(OFFICE_COLUMNS) & set(loaded["characters"]["p2"])
    db.close()
