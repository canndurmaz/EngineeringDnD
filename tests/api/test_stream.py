import json
import queue

import pytest

from app import sse_frame
from broker import EventBroker
from tests.api.conftest import make_room


def test_broker_delivers_to_a_subscriber():
    broker = EventBroker()
    q = broker.subscribe("r1")
    broker.publish("r1", {"seq": 1, "kind": "action"})
    assert q.get(timeout=1)["seq"] == 1


def test_broker_delivers_to_every_subscriber():
    broker = EventBroker()
    a, b = broker.subscribe("r1"), broker.subscribe("r1")
    broker.publish("r1", {"seq": 1})
    assert a.get(timeout=1)["seq"] == 1 and b.get(timeout=1)["seq"] == 1


def test_broker_does_not_cross_rooms():
    broker = EventBroker()
    a = broker.subscribe("r1")
    broker.publish("r2", {"seq": 1})
    with pytest.raises(queue.Empty):
        a.get(timeout=0.1)


def test_unsubscribe_stops_delivery():
    broker = EventBroker()
    q = broker.subscribe("r1")
    broker.unsubscribe("r1", q)
    broker.publish("r1", {"seq": 1})
    assert broker.subscriber_count("r1") == 0


def test_publish_to_a_room_with_no_subscribers_is_safe():
    EventBroker().publish("nobody", {"seq": 1})


def test_stream_returns_the_event_stream_content_type(client):
    room_id = make_room(client)
    response = client.get(f"/api/rooms/{room_id}/stream?once=1")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")


def test_stream_replays_history_from_zero(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    body = client.get(f"/api/rooms/{room_id}/stream?once=1&since=0").get_data(as_text=True)
    assert "event: room_created" in body
    assert "event: player_joined" in body


def test_stream_honours_the_since_parameter(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    latest = client.get(f"/api/rooms/{room_id}/state").get_json()["latest_seq"]
    body = client.get(
        f"/api/rooms/{room_id}/stream?once=1&since={latest}").get_data(as_text=True)
    assert "event: player_joined" not in body


def test_stream_honours_the_last_event_id_header(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    latest = client.get(f"/api/rooms/{room_id}/state").get_json()["latest_seq"]
    body = client.get(f"/api/rooms/{room_id}/stream?once=1",
                      headers={"Last-Event-ID": str(latest)}).get_data(as_text=True)
    assert "event: player_joined" not in body


def test_replayed_frames_carry_ids_and_json_payloads(client):
    room_id = make_room(client)
    body = client.get(f"/api/rooms/{room_id}/stream?once=1&since=0").get_data(as_text=True)
    first = body.split("\n\n")[0].splitlines()
    assert first[0].startswith("id: ")
    assert first[1].startswith("event: ")
    payload = json.loads(first[2][len("data: "):])
    assert "seq" in payload and "kind" in payload


def test_stream_for_an_unknown_room_is_a_400(client):
    assert client.get("/api/rooms/zzzzzz/stream?once=1").status_code == 400


# --- frame ids ------------------------------------------------------------------

def test_a_durable_frame_carries_an_id_line():
    frame = sse_frame(7, "action", {"outcome": "hit"})
    assert frame.startswith("id: 7\n")
    assert "event: action\n" in frame


def test_a_narration_chunk_frame_has_no_id_line():
    """A chunk is stamped with the action's seq, below the latest durable one.

    Stamping it as an id would rewind a client that reconnects with
    Last-Event-ID and make it replay events it has already rendered.
    """
    frame = sse_frame(7, "narration_chunk", {"delta": "the "})
    assert "id:" not in frame
    assert frame.startswith("event: narration_chunk\n")
    assert json.loads(frame.splitlines()[1][len("data: "):])["seq"] == 7
