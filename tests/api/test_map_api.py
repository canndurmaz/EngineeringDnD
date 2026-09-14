"""The system map as the server hands it out.

The map is broadcast to everyone at the table and to anyone watching, so the
sharpest test here is the negative one: nothing the party has not revealed may
ride along on it.
"""
import json

from app import _system_map
from tests.api.conftest import make_room


def state_of(client, room_id):
    return client.get(f"/api/rooms/{room_id}/state").get_json()


def test_the_state_carries_a_node_per_subsystem(client):
    room_id = make_room(client, archetype="aircraft")
    nodes = state_of(client, room_id)["map"]["nodes"]
    assert [n["id"] for n in nodes] == [
        "airframe", "propulsion", "avionics", "controls", "gear", "cabin"]
    assert all(n["name"] and n["blurb"] for n in nodes)


def test_every_node_carries_one_of_the_four_states(client):
    room_id = make_room(client)
    nodes = state_of(client, room_id)["map"]["nodes"]
    assert {n["status"] for n in nodes} <= {"clear", "open", "done", "active"}


def test_exactly_one_node_is_active_while_a_hazard_is_open(client):
    room_id = make_room(client)
    payload = state_of(client, room_id)
    active = [n for n in payload["map"]["nodes"] if n["status"] == "active"]
    assert len(active) == 1
    assert payload["map"]["active_subsystem"] == active[0]["id"]


def test_a_node_with_no_work_this_phase_is_clear(client):
    """Five to seven subsystems, three or four problems: something is always
    dim, which is what makes the lit ones mean anything."""
    room_id = make_room(client)
    nodes = state_of(client, room_id)["map"]["nodes"]
    assert any(n["status"] == "clear" for n in nodes)


def _hazards(*specs):
    """(subsystem, phase_index, defeated) triples as hazard rows."""
    return [{"id": f"h{i}", "phase_index": phase, "subsystem": sub,
             "defeated": done, "name": f"Problem {i}", "description": "d",
             "dc": 12, "weakness": "RIGOR", "attack_type": "stress",
             "revealed": [], "is_boss": False, "severity": 5,
             "max_severity": 5, "ordinal": i}
            for i, (sub, phase, done) in enumerate(specs)]


SUBS = [{"id": "a", "name": "A", "blurb": "b"},
        {"id": "b", "name": "B", "blurb": "b"},
        {"id": "c", "name": "C", "blurb": "b"}]


def _map(hazards, active_id):
    state = {"room": {"phase_index": 0}, "hazards": hazards,
             "active_hazard_id": active_id}
    return {n["id"]: n["status"] for n in _system_map(state, SUBS)["nodes"]}


def test_a_node_with_nothing_in_it_is_dim():
    assert _map(_hazards(("a", 0, False)), "h0")["c"] == "clear"


def test_the_node_holding_the_active_hazard_is_amber():
    assert _map(_hazards(("a", 0, False)), "h0")["a"] == "active"


def test_a_node_with_an_undefeated_hazard_that_is_not_active_is_red():
    statuses = _map(_hazards(("a", 0, False), ("b", 0, False)), "h0")
    assert statuses["b"] == "open"


def test_a_node_whose_every_hazard_is_defeated_is_green():
    statuses = _map(_hazards(("a", 0, False), ("b", 0, True)), "h0")
    assert statuses["b"] == "done"


def test_one_defeated_and_one_open_in_a_node_is_still_red():
    statuses = _map(_hazards(("a", 0, False), ("b", 0, True), ("b", 0, False)),
                    "h0")
    assert statuses["b"] == "open"


def test_another_phases_hazards_do_not_light_a_node():
    assert _map(_hazards(("a", 0, False), ("b", 3, False)), "h0")["b"] == "clear"


def test_an_archetype_with_no_subsystems_yields_an_empty_map():
    state = {"room": {"phase_index": 0}, "hazards": _hazards(("a", 0, False)),
             "active_hazard_id": "h0"}
    assert _system_map(state, [])["nodes"] == []


def test_the_map_leaks_no_unrevealed_hazard_field(app, client):
    """The one rule the map must not break."""
    room_id = make_room(client)
    payload = state_of(client, room_id)
    text = json.dumps(payload["map"])
    state = app.service.snapshot(room_id)
    for hazard in state["hazards"]:
        assert hazard["name"] not in text
        assert hazard["description"] not in text
        assert hazard["weakness"] not in text
        assert hazard["attack_type"] not in text
    # and no node carries a mechanics key at all
    for node in payload["map"]["nodes"]:
        assert set(node) == {"id", "name", "blurb", "status", "problems", "open"}


def test_the_map_never_names_the_hazards_it_counts(client):
    room_id = make_room(client)
    nodes = state_of(client, room_id)["map"]["nodes"]
    lit = [n for n in nodes if n["status"] in ("open", "active")]
    assert lit and all(isinstance(n["problems"], int) and n["problems"] >= 1
                       for n in lit)


def test_a_spectator_sees_the_same_map_as_a_player(client):
    room_id = make_room(client)
    watching = state_of(client, room_id)["map"]
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "mechanical_engineer"})
    seated = state_of(client, room_id)["map"]
    assert watching == seated
