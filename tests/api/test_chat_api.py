"""Party chat over the API: who may speak, what counts as a message, and the
fact that a line reaches both the stream and the log.
"""
import json

from tests.api.conftest import make_room


def seat(client, room_id, name="Ada", class_id="mechanical_engineer"):
    return client.post(f"/api/rooms/{room_id}/join",
                       json={"display_name": name, "class_id": class_id})


# --- who may speak ----------------------------------------------------------

def test_a_spectator_cannot_talk(client):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/chat", json={"body": "hello"})
    assert response.status_code == 403


def test_a_seated_engineer_can_talk(client):
    room_id = make_room(client)
    seat(client, room_id)
    response = client.post(f"/api/rooms/{room_id}/chat",
                           json={"body": "the pack runs hot"})
    assert response.status_code == 200
    assert response.get_json()["body"] == "the pack runs hot"
    assert response.get_json()["name"] == "Ada"


def test_a_spectator_may_still_read_the_conversation(client):
    room_id = make_room(client)
    seat(client, room_id)
    client.post(f"/api/rooms/{room_id}/chat", json={"body": "anyone there"})
    with client.session_transaction() as session:
        session.clear()                          # give up the seat, keep reading
    response = client.get(f"/api/rooms/{room_id}/chat")
    assert response.status_code == 200
    assert [m["body"] for m in response.get_json()["messages"]] == ["anyone there"]


# --- what counts as a message -----------------------------------------------

def test_an_empty_body_is_rejected(client):
    room_id = make_room(client)
    seat(client, room_id)
    assert client.post(f"/api/rooms/{room_id}/chat",
                       json={"body": ""}).status_code == 400


def test_a_whitespace_only_body_is_rejected(client):
    room_id = make_room(client)
    seat(client, room_id)
    assert client.post(f"/api/rooms/{room_id}/chat",
                       json={"body": "   \n\t "}).status_code == 400


def test_five_hundred_characters_is_allowed(client):
    room_id = make_room(client)
    seat(client, room_id)
    assert client.post(f"/api/rooms/{room_id}/chat",
                       json={"body": "x" * 500}).status_code == 200


def test_more_than_five_hundred_characters_is_rejected(client):
    room_id = make_room(client)
    seat(client, room_id)
    response = client.post(f"/api/rooms/{room_id}/chat",
                           json={"body": "x" * 501})
    assert response.status_code == 400
    assert "500" in response.get_json()["error"]


def test_a_non_string_body_does_not_five_hundred(client):
    """Same rule as every other endpoint: a wrong body is a 400, not a crash."""
    room_id = make_room(client)
    seat(client, room_id)
    for body in ({"body": {"a": 1}}, {"body": [1, 2]}, {"body": 5}, {}, []):
        assert client.post(f"/api/rooms/{room_id}/chat",
                           json=body).status_code == 400


def test_the_sender_name_comes_from_the_seat_not_the_body(client):
    """A body must not be able to forge who said something."""
    room_id = make_room(client)
    seat(client, room_id, name="Ada")
    response = client.post(f"/api/rooms/{room_id}/chat",
                           json={"body": "hi", "name": "Somebody Else",
                                 "is_bot": True})
    assert response.get_json()["name"] == "Ada"
    assert response.get_json()["is_bot"] is False


# --- reading it back --------------------------------------------------------

def test_a_message_appears_in_the_listing(client):
    room_id = make_room(client)
    seat(client, room_id)
    client.post(f"/api/rooms/{room_id}/chat", json={"body": "first"})
    client.post(f"/api/rooms/{room_id}/chat", json={"body": "second"})
    bodies = [m["body"] for m in
              client.get(f"/api/rooms/{room_id}/chat").get_json()["messages"]]
    assert bodies == ["first", "second"]


def test_since_returns_only_what_came_after(client):
    room_id = make_room(client)
    seat(client, room_id)
    first = client.post(f"/api/rooms/{room_id}/chat",
                        json={"body": "first"}).get_json()
    client.post(f"/api/rooms/{room_id}/chat", json={"body": "second"})
    listing = client.get(
        f"/api/rooms/{room_id}/chat?since={first['id']}").get_json()
    assert [m["body"] for m in listing["messages"]] == ["second"]


def test_a_malformed_since_is_not_an_error(client):
    room_id = make_room(client)
    seat(client, room_id)
    client.post(f"/api/rooms/{room_id}/chat", json={"body": "first"})
    response = client.get(f"/api/rooms/{room_id}/chat?since=banana")
    assert response.status_code == 200
    assert len(response.get_json()["messages"]) == 1


def test_an_unknown_room_is_a_four_hundred(client):
    assert client.get("/api/rooms/nosuch/chat").status_code == 400


# --- the stream and the log -------------------------------------------------

def test_a_message_is_published_on_the_stream(client):
    room_id = make_room(client)
    seat(client, room_id)
    client.post(f"/api/rooms/{room_id}/chat", json={"body": "on the wire"})
    stream = client.get(
        f"/api/rooms/{room_id}/stream?since=0&once=1").get_data(as_text=True)
    assert "event: chat" in stream
    assert "on the wire" in stream


def test_the_line_is_in_the_event_log_for_a_reconnecting_client(app, client):
    room_id = make_room(client)
    seat(client, room_id)
    client.post(f"/api/rooms/{room_id}/chat", json={"body": "replay me"})
    kinds = [e["kind"] for e in app.service.events_since(room_id, 0)]
    assert "chat" in kinds
    chat = [e for e in app.service.events_since(room_id, 0)
            if e["kind"] == "chat"][0]
    assert chat["payload"]["body"] == "replay me"
    assert chat["payload"]["message_id"]


def test_a_script_tag_survives_storage_unchanged(client):
    """The server stores what was typed; escaping is the renderer's job, and
    game.js is tested for it separately. What must not happen is the server
    quietly mangling or trusting it."""
    room_id = make_room(client)
    seat(client, room_id)
    payload = "<script>alert(1)</script>"
    client.post(f"/api/rooms/{room_id}/chat", json={"body": payload})
    listing = client.get(f"/api/rooms/{room_id}/chat").get_json()
    assert listing["messages"][0]["body"] == payload
    # and it goes out on the stream as JSON, not as markup
    # On the wire it is a JSON string inside an SSE data field -- never markup
    # the browser parses. The one place it becomes DOM is game.js, through esc().
    stream = client.get(
        f"/api/rooms/{room_id}/stream?since=0&once=1").get_data(as_text=True)
    frame = [line for line in stream.splitlines() if line.startswith("data: ")]
    assert any(json.loads(line[6:]).get("body") == payload for line in frame)
