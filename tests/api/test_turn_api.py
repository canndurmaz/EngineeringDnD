import pytest
from tests.api.conftest import make_room


@pytest.fixture
def table(app):
    """A started two-player room; returns (room_id, ada_client, ben_client)."""
    ada, ben = app.test_client(), app.test_client()
    room_id = make_room(ada)
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ben.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ben", "class_id": "mechanical_technician"})
    ada.post(f"/api/rooms/{room_id}/start")
    return room_id, ada, ben


def test_action_returns_a_roll_result(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "unit_test_barrage"})
    assert response.status_code == 200
    result = response.get_json()["result"]
    assert 1 <= result["natural"] <= 20
    assert result["outcome"] in ("crit", "success", "failure", "fumble")


def test_action_returns_the_events_it_produced(table):
    room_id, ada, _ = table
    events = ada.post(f"/api/rooms/{room_id}/action",
                      json={"ability_id": "unit_test_barrage"}).get_json()["events"]
    assert any(e["kind"] == "action" for e in events)


def test_action_advances_the_turn_to_the_next_player(table):
    room_id, ada, ben = table
    ada.post(f"/api/rooms/{room_id}/action", json={"ability_id": "unit_test_barrage"})
    state = ben.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["turn"]["active_player_id"] == state["you"]["player_id"]


def test_acting_out_of_turn_is_a_400_with_a_readable_message(table):
    room_id, _, ben = table
    response = ben.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "shop_floor_fix"})
    assert response.status_code == 400
    assert "turn" in response.get_json()["error"].lower()


def test_acting_without_a_seat_is_a_403(app, table):
    room_id, _, _ = table
    stranger = app.test_client()
    response = stranger.post(f"/api/rooms/{room_id}/action",
                             json={"ability_id": "unit_test_barrage"})
    assert response.status_code == 403


def test_using_a_locked_ability_is_a_400(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "rubber_duck"})
    assert response.status_code == 400
    assert "unlocked" in response.get_json()["error"]


def test_using_an_unknown_ability_is_a_400(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "fireball"})
    assert response.status_code == 400


def test_action_without_an_ability_id_is_a_400(table):
    room_id, ada, _ = table
    assert ada.post(f"/api/rooms/{room_id}/action", json={}).status_code == 400


def test_end_turn_passes_the_turn(table):
    room_id, ada, ben = table
    assert ada.post(f"/api/rooms/{room_id}/end-turn").status_code == 200
    state = ben.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["turn"]["active_player_id"] == state["you"]["player_id"]


def test_end_turn_out_of_turn_is_a_400(table):
    room_id, _, ben = table
    assert ben.post(f"/api/rooms/{room_id}/end-turn").status_code == 400


def test_a_full_round_produces_a_hazard_attack_event(table):
    room_id, ada, ben = table
    ada.post(f"/api/rooms/{room_id}/end-turn")
    events = ben.post(f"/api/rooms/{room_id}/end-turn").get_json()["events"]
    assert any(e["kind"] == "hazard_attack" for e in events)


def test_focus_is_visibly_spent_after_acting(table):
    room_id, ada, _ = table
    before = ada.get(f"/api/rooms/{room_id}/state").get_json()["you"]["focus"]
    ada.post(f"/api/rooms/{room_id}/action", json={"ability_id": "unit_test_barrage"})
    after = ada.get(f"/api/rooms/{room_id}/state").get_json()["you"]["focus"]
    assert after == before - 1


def test_insufficient_focus_is_a_readable_400(app, table):
    room_id, ada, _ = table
    # Drain the active character's Focus directly. Looping real turns cannot exhaust it:
    # every phase-0 starter either regenerates as fast as it costs (unit_test_barrage, 1
    # Focus against +1/turn) or is once-per-hazard (binary_search_debug).
    room = app.service._room(room_id)
    state = room.load_state()
    active = state["turn"]["order"][state["turn"]["turn_index"]]
    state["characters"][active]["focus"] = 0
    room.save_state(state)

    response = ada.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "binary_search_debug"})
    assert response.status_code == 400
    assert "Focus" in response.get_json()["error"]
