import pytest
from narrator.queue_ import NarrationQueue


def test_get_on_an_empty_queue_returns_none():
    assert NarrationQueue().get(timeout=0.01) is None


def test_submitted_job_comes_back():
    q = NarrationQueue()
    q.submit({"kind": "turn", "priority": 0, "room_id": "r"})
    assert q.get(timeout=1)["kind"] == "turn"


def test_lower_priority_number_is_served_first():
    q = NarrationQueue()
    q.submit({"kind": "genesis", "priority": 2, "room_id": "r"})
    q.submit({"kind": "turn", "priority": 0, "room_id": "r"})
    assert q.get(timeout=1)["kind"] == "turn"


def test_equal_priority_is_first_in_first_out():
    q = NarrationQueue()
    q.submit({"kind": "turn", "priority": 0, "room_id": "r", "n": 1})
    q.submit({"kind": "turn", "priority": 0, "room_id": "r", "n": 2})
    assert [q.get(timeout=1)["n"], q.get(timeout=1)["n"]] == [1, 2]


def test_pending_counts_waiting_jobs():
    q = NarrationQueue()
    q.submit({"kind": "turn", "priority": 0, "room_id": "r"})
    q.submit({"kind": "turn", "priority": 0, "room_id": "r"})
    assert q.pending() == 2


def test_drop_room_discards_only_that_rooms_jobs():
    q = NarrationQueue()
    q.submit({"kind": "turn", "priority": 0, "room_id": "a"})
    q.submit({"kind": "turn", "priority": 0, "room_id": "b"})
    q.drop_room("a")
    assert q.get(timeout=1)["room_id"] == "b"
    assert q.get(timeout=0.01) is None


def test_job_without_a_priority_defaults_to_normal():
    q = NarrationQueue()
    q.submit({"kind": "turn", "room_id": "r"})
    assert q.get(timeout=1) is not None
