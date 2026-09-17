"""The office over HTTP: /office/move, /office/act, /office/desk, the state
block, and the vendored Phaser build."""
import pytest
from engine.places import DESK_DEFAULTS
from tests.api.conftest import make_room


@pytest.fixture
def table(app):
    ada, ben = app.test_client(), app.test_client()
    room_id = make_room(ada)
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ben.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ben", "class_id": "mechanical_technician"})
    ada.post(f"/api/rooms/{room_id}/start")
    return room_id, ada, ben


def me(client, room_id):
    return client.get(f"/api/rooms/{room_id}/state").get_json()["you"]["player_id"]


def events(client, room_id):
    body = client.get(f"/api/rooms/{room_id}/stream?once=1").get_data(as_text=True)
    return [line.split(": ", 1)[1] for line in body.splitlines()
            if line.startswith("event: ")]


# --- move -------------------------------------------------------------------

def test_move_changes_the_zone_and_broadcasts(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/office/move", json={"zone": "lab"})
    assert response.status_code == 200
    assert response.get_json() == {"zone": "lab", "moved": True}
    state = ada.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["you"]["office_zone"] == "lab"
    assert state["you"]["office_action"] == "bench_test"
    assert state["office"]["positions"][state["you"]["player_id"]] == "lab"
    assert "office_move" in events(ada, room_id)


def test_move_costs_no_turn(table):
    room_id, ada, _ = table
    before = ada.get(f"/api/rooms/{room_id}/state").get_json()["turn"]
    ada.post(f"/api/rooms/{room_id}/office/move", json={"zone": "break_room"})
    after = ada.get(f"/api/rooms/{room_id}/state").get_json()["turn"]
    assert before == after


@pytest.mark.parametrize("body", [{"zone": "roof"}, {"zone": 5}, {},
                                  {"zone": ["lab"]}, {"zone": "desk:pnobody"}])
def test_move_to_an_unknown_zone_is_a_400(table, body):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/office/move", json=body)
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_a_spectator_cannot_move_act_or_decorate(app, table):
    room_id, _, _ = table
    stranger = app.test_client()
    for path, body in (("move", {"zone": "lab"}),
                       ("act", {"action": "bench_test"}),
                       ("desk", {"monitor": "dual"})):
        response = stranger.post(f"/api/rooms/{room_id}/office/{path}", json=body)
        assert response.status_code == 403


def test_malformed_bodies_are_400s_not_500s(table):
    room_id, ada, _ = table
    for path in ("move", "act"):
        for body in ([1], "x", 3, None):
            response = ada.post(f"/api/rooms/{room_id}/office/{path}", json=body)
            assert response.status_code == 400, (path, body)
    response = ada.post(f"/api/rooms/{room_id}/office/desk", json=[1])
    assert response.status_code == 200
    assert response.get_json()["desk"] == DESK_DEFAULTS


# --- act --------------------------------------------------------------------

def test_bench_test_over_http(table):
    room_id, ada, ben = table
    ada.post(f"/api/rooms/{room_id}/office/move", json={"zone": "lab"})
    response = ada.post(f"/api/rooms/{room_id}/office/act",
                        json={"action": "bench_test"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["result"]["changes"][0]["kind"] == "reveal"
    assert "place_action" in [e["kind"] for e in body["events"]]
    state = ben.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["hazard"]["weakness"] is not None
    assert state["turn"]["active_player_id"] == me(ben, room_id)


def test_act_in_the_wrong_zone_is_a_400(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/office/act",
                        json={"action": "bench_test"})
    assert response.status_code == 400
    assert "lab" in response.get_json()["error"]


def test_act_out_of_turn_is_a_400(table):
    room_id, _, ben = table
    ben.post(f"/api/rooms/{room_id}/office/move", json={"zone": "lab"})
    response = ben.post(f"/api/rooms/{room_id}/office/act",
                        json={"action": "bench_test"})
    assert response.status_code == 400
    assert "turn" in response.get_json()["error"].lower()


def test_pair_up_over_http(table):
    room_id, ada, ben = table
    ben_id = me(ben, room_id)
    ada.post(f"/api/rooms/{room_id}/office/move", json={"zone": f"desk:{ben_id}"})
    response = ada.post(f"/api/rooms/{room_id}/office/act",
                        json={"action": "pair_up", "target_id": ben_id})
    assert response.status_code == 200
    state = ben.get(f"/api/rooms/{room_id}/state").get_json()
    mine = [c for c in state["conditions"] if c.get("target_id") == ben_id]
    assert mine and mine[0]["name"] == "roll_bonus"


def test_unknown_action_is_a_400(table):
    room_id, ada, _ = table
    for action in ("", "nap", "customize"):
        response = ada.post(f"/api/rooms/{room_id}/office/act",
                            json={"action": action})
        assert response.status_code == 400


# --- desk -------------------------------------------------------------------

def test_desk_customisation_is_validated_and_visible_to_everyone(table):
    room_id, ada, ben = table
    response = ben.post(f"/api/rooms/{room_id}/office/desk",
                        json={"desk_color": "teal", "monitor": "dual",
                              "plant": "<script>alert(1)</script>",
                              "poster": "motivational"})
    assert response.status_code == 200
    desk = response.get_json()["desk"]
    assert desk == {**DESK_DEFAULTS, "desk_color": "teal", "monitor": "dual",
                    "poster": "motivational"}
    state = ada.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["characters"][me(ben, room_id)]["desk"] == desk
    owner = next(d for d in state["office"]["desks"]
                 if d["player_id"] == me(ben, room_id))
    assert owner["desk"] == desk
    assert "desk_updated" in events(ada, room_id)


def test_desk_accepts_a_nested_desk_object(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/office/desk",
                        json={"desk": {"mug": "coffee"}})
    assert response.get_json()["desk"]["mug"] == "coffee"


# --- state ------------------------------------------------------------------

def test_state_carries_office_defaults_for_every_character(table):
    room_id, ada, _ = table
    state = ada.get(f"/api/rooms/{room_id}/state").get_json()
    for char in state["characters"].values():
        assert char["office_zone"] == "floor"
        assert char["desk"] == DESK_DEFAULTS
    office = state["office"]
    assert office["fixed_zones"] == ["floor", "lab", "break_room", "whiteboard"]
    assert len(office["desks"]) == 2
    assert office["actions"]["bench_test"] == "Bench Test"
    assert state["you"]["coffee_used"] == 0


def test_a_spectator_sees_the_office_too(app, table):
    room_id, _, _ = table
    state = app.test_client().get(f"/api/rooms/{room_id}/state").get_json()
    assert state["you"] is None
    assert len(state["office"]["zones"]) == 6


# --- phaser -----------------------------------------------------------------

def test_phaser_is_served_locally(client):
    response = client.get("/static/vendor/phaser.min.js")
    assert response.status_code == 200
    assert len(response.data) > 900_000
    assert b"Phaser" in response.data[:2000]
