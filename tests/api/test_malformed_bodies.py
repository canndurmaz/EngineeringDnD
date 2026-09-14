"""An illegal request body is a 400, never a 500.

Every JSON body on the wire is attacker-controlled, and JSON's top level can be
a list, a number or a string as easily as an object. Each of these once reached
`.get()` or `.strip()` on a non-object and crashed the request.
"""
import pytest
from tests.api.conftest import make_room


BAD_BODIES = [[1, 2], "just a string", 7, None, True]


@pytest.mark.parametrize("body", BAD_BODIES)
def test_create_room_rejects_a_non_object_body(client, body):
    assert client.post("/api/rooms", json=body).status_code == 400


@pytest.mark.parametrize("name", [123, ["a"], {"x": 1}, 4.5])
def test_create_room_rejects_a_non_string_name(client, name):
    response = client.post("/api/rooms", json={"name": name,
                                               "archetype": "aircraft"})
    assert response.status_code == 201         # coerced to text, never crashed
    assert response.get_json()["room_id"]


def test_create_room_with_a_non_string_archetype_is_a_400(client):
    response = client.post("/api/rooms", json={"name": "Kestrel",
                                               "archetype": {"id": "aircraft"}})
    assert response.status_code == 400


@pytest.mark.parametrize("body", BAD_BODIES)
def test_join_rejects_a_non_object_body(client, body):
    room_id = make_room(client)
    assert client.post(f"/api/rooms/{room_id}/join",
                       json=body).status_code == 400


@pytest.mark.parametrize("display_name", [123, ["Ada"], {"n": "Ada"}])
def test_join_rejects_a_non_string_display_name(client, display_name):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/join",
                           json={"display_name": display_name,
                                 "class_id": "computer_scientist"})
    assert response.status_code == 200         # coerced to text, never crashed


def test_join_with_a_non_string_class_id_is_a_400(client):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/join",
                           json={"display_name": "Ada", "class_id": [1, 2]})
    assert response.status_code == 400


def test_join_with_a_non_object_appearance_still_joins(client):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/join",
                           json={"display_name": "Ada",
                                 "class_id": "computer_scientist",
                                 "appearance": ["hair"]})
    assert response.status_code == 200


def _seated(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    return room_id


@pytest.mark.parametrize("body", BAD_BODIES)
def test_level_choice_rejects_a_non_object_body(client, body):
    room_id = _seated(client)
    assert client.post(f"/api/rooms/{room_id}/level-choice",
                       json=body).status_code == 400


def test_level_choice_with_a_non_string_stat_is_a_400(client):
    room_id = _seated(client)
    assert client.post(f"/api/rooms/{room_id}/level-choice",
                       json={"stat": ["GRIT"]}).status_code == 400


@pytest.mark.parametrize("body", BAD_BODIES)
def test_action_rejects_a_non_object_body(client, body):
    room_id = _seated(client)
    client.post(f"/api/rooms/{room_id}/start")
    assert client.post(f"/api/rooms/{room_id}/action",
                       json=body).status_code == 400


@pytest.mark.parametrize("ability_id", [123, ["a"], {"id": "a"}])
def test_action_with_a_non_string_ability_is_a_400(client, ability_id):
    room_id = _seated(client)
    client.post(f"/api/rooms/{room_id}/start")
    assert client.post(f"/api/rooms/{room_id}/action",
                       json={"ability_id": ability_id}).status_code == 400


def test_action_with_a_non_string_target_does_not_crash(client):
    room_id = _seated(client)
    client.post(f"/api/rooms/{room_id}/start")
    response = client.post(f"/api/rooms/{room_id}/action",
                           json={"ability_id": "unit_test_barrage",
                                 "target_id": [1, 2]})
    assert response.status_code == 200         # coerced to text, never crashed
