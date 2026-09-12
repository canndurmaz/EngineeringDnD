import pytest
from storage.index_db import IndexDB
from storage.room_db import RoomDB


@pytest.fixture
def index(tmp_path):
    db = IndexDB(str(tmp_path))
    yield db
    db.close()


def test_empty_index_lists_nothing(index):
    assert index.list_rooms() == []


def test_upsert_then_list(index):
    index.upsert("abc", "Kestrel", "aircraft", 0, "lobby", 2)
    rooms = index.list_rooms()
    assert len(rooms) == 1
    assert rooms[0]["room_id"] == "abc" and rooms[0]["player_count"] == 2


def test_upsert_is_idempotent_and_updates_in_place(index):
    index.upsert("abc", "Kestrel", "aircraft", 0, "lobby", 2)
    index.upsert("abc", "Kestrel", "aircraft", 3, "active", 4)
    rooms = index.list_rooms()
    assert len(rooms) == 1
    assert rooms[0]["phase_index"] == 3 and rooms[0]["status"] == "active"


def test_list_is_ordered_most_recently_active_first(index):
    index.upsert("old", "Old", "car", 0, "lobby", 1)
    index.upsert("new", "New", "car", 0, "lobby", 1)
    assert [r["room_id"] for r in index.list_rooms()] == ["new", "old"]


def test_remove_deletes_the_row(index):
    index.upsert("abc", "Kestrel", "aircraft", 0, "lobby", 1)
    index.remove("abc")
    assert index.list_rooms() == []


def test_rebuild_reconstructs_from_room_databases(tmp_path, index):
    RoomDB.create(str(tmp_path), "r1", "Kestrel", "aircraft", 1).close()
    RoomDB.create(str(tmp_path), "r2", "Osprey", "car", 2).close()
    assert index.rebuild() == 2
    assert {r["room_id"] for r in index.list_rooms()} == {"r1", "r2"}


def test_rebuild_drops_rows_for_rooms_that_no_longer_exist(tmp_path, index):
    index.upsert("ghost", "Ghost", "car", 0, "lobby", 1)
    assert index.rebuild() == 0
    assert index.list_rooms() == []


def test_rebuild_captures_player_counts(tmp_path, index):
    room = RoomDB.create(str(tmp_path), "r1", "Kestrel", "aircraft", 1)
    room.add_player("p1", "Ada", "t1", "computer_scientist")
    room.add_player("p2", "Ben", "t2", "mechanical_technician")
    room.close()
    index.rebuild()
    assert index.list_rooms()[0]["player_count"] == 2


def test_index_file_lives_beside_the_rooms(tmp_path, index):
    index.upsert("abc", "K", "car", 0, "lobby", 1)
    assert (tmp_path / "index.db").exists()


def test_rebuild_skips_corrupt_databases(tmp_path, index):
    """Verify rebuild() gracefully handles corrupt room databases."""
    # Create two good rooms
    RoomDB.create(str(tmp_path), "r1", "Kestrel", "aircraft", 1).close()
    RoomDB.create(str(tmp_path), "r2", "Osprey", "car", 2).close()

    # Create a third room, then corrupt its database
    room = RoomDB.create(str(tmp_path), "r3", "Broken", "car", 0)
    room.close()
    corrupt_db = tmp_path / "r3" / "game.db"
    corrupt_db.write_bytes(b"not a database")

    # rebuild() should return 2 (only the good rooms), not crash
    assert index.rebuild() == 2

    # The two good rooms should be indexed
    room_ids = {r["room_id"] for r in index.list_rooms()}
    assert room_ids == {"r1", "r2"}

    # The corrupt room should NOT be in the index
    assert "r3" not in room_ids
