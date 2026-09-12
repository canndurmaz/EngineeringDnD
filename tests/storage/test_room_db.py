import json
import pytest
from storage.room_db import RoomDB, RoomNotFound
from tests.engine.test_effects import make_state


@pytest.fixture
def room(tmp_path):
    db = RoomDB.create(str(tmp_path), "abc123", "Kestrel", "aircraft", 4242)
    yield db
    db.close()


def seat(room, state):
    """Characters FK to players, so a forged state needs its players seated first."""
    for player_id, char in state["characters"].items():
        room.add_player(player_id, char["name"], f"tok-{player_id}", char["class_id"])


def test_create_makes_the_file(tmp_path, room):
    assert (tmp_path / "abc123" / "game.db").exists()


def test_exists_reports_presence(tmp_path, room):
    assert RoomDB.exists(str(tmp_path), "abc123") is True
    assert RoomDB.exists(str(tmp_path), "nope") is False


def test_open_missing_room_raises(tmp_path):
    with pytest.raises(RoomNotFound):
        RoomDB.open(str(tmp_path), "ghost")


def test_wal_mode_is_enabled(room):
    mode = room.connect().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_new_room_starts_with_spec_default_resources(room):
    state = room.load_state()
    assert state["party"] == {"budget": 100, "schedule": 100, "tech_debt": 0}


def test_new_room_records_its_identity(room):
    state = room.load_state()
    assert state["room"]["id"] == "abc123"
    assert state["room"]["name"] == "Kestrel"
    assert state["room"]["archetype"] == "aircraft"
    assert state["room"]["rng_seed"] == 4242
    assert state["room"]["phase_index"] == 0
    assert state["room"]["status"] == "lobby"


def test_state_round_trips_unchanged(room):
    original = make_state()
    original["room"].update({"id": "abc123", "name": "Kestrel",
                             "archetype": "aircraft", "rng_seed": 4242,
                             "premise": "A trainer aircraft."})
    seat(room, original)
    room.save_state(original)
    assert room.load_state() == original


def test_state_survives_closing_and_reopening(tmp_path, room):
    s = make_state()
    s["room"].update({"id": "abc123", "name": "Kestrel", "archetype": "aircraft",
                      "rng_seed": 4242, "premise": "p"})
    s["party"]["tech_debt"] = 17
    seat(room, s)
    room.save_state(s)
    room.close()
    reopened = RoomDB.open(str(tmp_path), "abc123")
    assert reopened.load_state()["party"]["tech_debt"] == 17
    reopened.close()


def test_save_state_replaces_rather_than_duplicating_hazards(room):
    s = make_state()
    s["room"].update({"id": "abc123", "name": "K", "archetype": "aircraft",
                      "rng_seed": 1, "premise": "p"})
    seat(room, s)
    room.save_state(s)
    room.save_state(s)
    assert len(room.load_state()["hazards"]) == len(s["hazards"])


def test_add_player_and_lookup_by_token(room):
    room.add_player("p1", "Ada", "tok-1", "computer_scientist")
    found = room.player_by_token("tok-1")
    assert found["player_id"] == "p1" and found["class_id"] == "computer_scientist"


def test_player_by_unknown_token_is_none(room):
    assert room.player_by_token("nope") is None


def test_players_lists_everyone_in_join_order(room):
    room.add_player("p1", "Ada", "t1", "computer_scientist")
    room.add_player("p2", "Ben", "t2", "mechanical_technician")
    assert [p["player_id"] for p in room.players()] == ["p1", "p2"]


def test_append_event_returns_increasing_sequence_numbers(room):
    a = room.append_event("action", "p1", {"x": 1})
    b = room.append_event("action", "p1", {"x": 2})
    assert b == a + 1


def test_events_since_excludes_earlier_events(room):
    first = room.append_event("a", None, {})
    room.append_event("b", None, {})
    room.append_event("c", None, {})
    kinds = [e["kind"] for e in room.events_since(first)]
    assert kinds == ["b", "c"]


def test_events_since_zero_replays_everything(room):
    room.append_event("a", None, {})
    room.append_event("b", None, {})
    assert len(room.events_since(0)) == 2


def test_event_payload_round_trips_as_json(room):
    seq = room.append_event("roll", "p1", {"natural": 17, "tags": ["crit"]})
    event = room.events_since(seq - 1)[0]
    assert event["payload"] == {"natural": 17, "tags": ["crit"]}


def test_latest_seq_tracks_the_last_append(room):
    assert room.latest_seq() == 0
    seq = room.append_event("a", None, {})
    assert room.latest_seq() == seq


def test_narration_lifecycle_from_pending_to_done(room):
    seq = room.append_event("action", "p1", {})
    room.create_narration(seq)
    assert room.narration(seq)["status"] == "pending"
    room.update_narration(seq, "done", "The bracket holds.", "llm")
    done = room.narration(seq)
    assert done["status"] == "done"
    assert done["text"] == "The bracket holds."
    assert done["source"] == "llm"


def test_narration_for_unknown_event_is_none(room):
    assert room.narration(999) is None


def test_list_room_ids_finds_created_rooms(tmp_path, room):
    RoomDB.create(str(tmp_path), "def456", "Osprey", "car", 1).close()
    assert sorted(RoomDB.list_room_ids(str(tmp_path))) == ["abc123", "def456"]


def test_list_room_ids_ignores_directories_without_a_database(tmp_path, room):
    (tmp_path / "junk").mkdir()
    assert "junk" not in RoomDB.list_room_ids(str(tmp_path))
