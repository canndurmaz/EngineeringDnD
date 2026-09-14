from tests.api.conftest import make_room


def test_archetypes_endpoint_lists_options(client):
    data = client.get("/api/archetypes").get_json()
    assert any(a["id"] == "aircraft" for a in data["archetypes"])


def test_classes_endpoint_lists_all_nine_with_abilities(client):
    data = client.get("/api/classes").get_json()
    assert len(data["classes"]) == 9
    for entry in data["classes"]:
        assert len(entry["abilities"]) == 4
        assert entry["primary"] and entry["role"]


def test_create_room_returns_an_id(client):
    response = client.post("/api/rooms", json={"name": "Kestrel",
                                               "archetype": "aircraft"})
    assert response.status_code == 201
    assert response.get_json()["room_id"]


def test_create_room_with_a_bad_archetype_is_a_400(client):
    response = client.post("/api/rooms", json={"name": "X", "archetype": "nope"})
    assert response.status_code == 400
    assert "archetype" in response.get_json()["error"]


def test_create_room_without_a_name_is_a_400(client):
    response = client.post("/api/rooms", json={"archetype": "aircraft"})
    assert response.status_code == 400


def test_rooms_listing_includes_a_created_room(client):
    room_id = make_room(client)
    rooms = client.get("/api/rooms").get_json()["rooms"]
    assert room_id in {r["room_id"] for r in rooms}


def test_join_seats_a_character(client):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/join",
                           json={"display_name": "Ada",
                                 "class_id": "computer_scientist"})
    assert response.status_code == 200
    assert response.get_json()["character"]["class_id"] == "computer_scientist"


def test_join_returns_the_roll_breakdown(client):
    """The 4d6 reveal rides on the join response -- and is never persisted."""
    room_id = make_room(client)
    body = client.post(f"/api/rooms/{room_id}/join",
                       json={"display_name": "Ada",
                             "class_id": "computer_scientist"}).get_json()
    roll = body["roll"]
    assert len(roll["rolls"]) == 6
    for entry in roll["rolls"]:
        assert len(entry["dice"]) == 4
        assert entry["dropped"] == min(entry["dice"])
    assert roll["primary"] and roll["secondary"]
    state = client.get(f"/api/rooms/{room_id}/state").get_json()
    stored = next(iter(state["characters"].values()))
    assert "roll" not in stored


def test_the_session_survives_across_requests(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    # A second, independent request still knows who you are: the cookie holds.
    assert client.get(f"/api/rooms/{room_id}/state").get_json()["you"] is not None


def test_state_identifies_the_joined_player(client):
    room_id = make_room(client)
    joined = client.post(f"/api/rooms/{room_id}/join",
                         json={"display_name": "Ada",
                               "class_id": "computer_scientist"}).get_json()
    state = client.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["you"]["player_id"] == joined["player_id"]


def test_state_for_an_anonymous_visitor_has_no_you(client):
    room_id = make_room(client)
    assert client.get(f"/api/rooms/{room_id}/state").get_json()["you"] is None


def test_joining_a_taken_class_is_a_400(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    other = client.application.test_client()
    response = other.post(f"/api/rooms/{room_id}/join",
                          json={"display_name": "Ben",
                                "class_id": "computer_scientist"})
    assert response.status_code == 400
    assert "taken" in response.get_json()["error"]


def test_joining_an_unknown_room_is_a_400(client):
    response = client.post("/api/rooms/zzzzzz/join",
                           json={"display_name": "Ada",
                                 "class_id": "computer_scientist"})
    assert response.status_code == 400


def test_start_activates_the_room(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    assert client.post(f"/api/rooms/{room_id}/start").status_code == 200
    assert client.get(f"/api/rooms/{room_id}/state").get_json()["room"]["status"] == "active"


def test_start_with_an_empty_room_is_rejected(client):
    """Nobody can hold a seat in an empty room, so the seat guard answers first."""
    room_id = make_room(client)
    assert client.post(f"/api/rooms/{room_id}/start").status_code == 403


def test_start_requires_a_seat(app):
    ada, stranger = app.test_client(), app.test_client()
    room_id = make_room(ada)
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    assert stranger.post(f"/api/rooms/{room_id}/start").status_code == 403
    assert ada.get(f"/api/rooms/{room_id}/state").get_json()["room"]["status"] \
        == "lobby"


def test_state_hides_unrevealed_hazard_fields(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    client.post(f"/api/rooms/{room_id}/start")
    hazard = client.get(f"/api/rooms/{room_id}/state").get_json()["hazard"]
    assert hazard["weakness"] is None and hazard["dc"] is None
    assert hazard["name"] and hazard["severity"]


def test_level_choice_accepts_a_valid_stat(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    response = client.post(f"/api/rooms/{room_id}/level-choice", json={"stat": "GRIT"})
    assert response.status_code == 200


def test_level_choice_rejects_an_unknown_stat(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    response = client.post(f"/api/rooms/{room_id}/level-choice", json={"stat": "LUCK"})
    assert response.status_code == 400


def test_level_choice_without_a_seat_is_a_403(client):
    room_id = make_room(client)
    assert client.post(f"/api/rooms/{room_id}/level-choice",
                       json={"stat": "GRIT"}).status_code == 403


# --- names are data, not markup ------------------------------------------------

XSS = "<img src=x onerror=alert(1)>"


def test_rooms_listing_returns_a_markup_name_as_data(client):
    """The API is JSON: it carries the payload verbatim and the client escapes it."""
    room_id = make_room(client, name=XSS)
    room = next(r for r in client.get("/api/rooms").get_json()["rooms"]
                if r["room_id"] == room_id)
    assert room["name"] == XSS          # intact as DATA, not HTML-escaped server-side
    # Served as JSON, so a browser never parses it as markup; escaping is the
    # client's job at the point it builds HTML (see esc() in static/js/lobby.js).
    response = client.get("/api/rooms")
    assert response.headers["Content-Type"].startswith("application/json")


def test_room_name_over_forty_characters_is_a_400(client):
    response = client.post("/api/rooms", json={"name": "x" * 41,
                                               "archetype": "aircraft"})
    assert response.status_code == 400
    assert "40" in response.get_json()["error"]


def test_room_name_of_exactly_forty_characters_is_allowed(client):
    response = client.post("/api/rooms", json={"name": "x" * 40,
                                               "archetype": "aircraft"})
    assert response.status_code == 201


def test_display_name_over_forty_characters_is_a_400(client):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/join",
                           json={"display_name": "x" * 41,
                                 "class_id": "computer_scientist"})
    assert response.status_code == 400
    assert "40" in response.get_json()["error"]
