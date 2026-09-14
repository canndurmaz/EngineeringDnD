"""A full game, driven through the HTTP API, with a deterministic narrator."""
import pytest
from app import create_app
from narrator.base import FakeNarrator
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker


@pytest.fixture
def rig(tmp_path):
    queue = NarrationQueue()
    app = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                      "TESTING": True, "NARRATION": queue})
    worker = NarrationWorker(queue, FakeNarrator("The bracket holds."),
                             app.service, app.broker)
    return app, worker


def test_a_room_can_be_created_joined_started_and_played(rig):
    app, worker = rig
    ada, ben = app.test_client(), app.test_client()

    room_id = ada.post("/api/rooms", json={"name": "Kestrel",
                                           "archetype": "aircraft"}).get_json()["room_id"]
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ben.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ben", "class_id": "mechanical_technician"})
    ada.post(f"/api/rooms/{room_id}/start")

    for _ in range(20):
        state = ada.get(f"/api/rooms/{room_id}/state").get_json()
        if state["room"]["status"] != "active":
            break
        active = state["turn"]["active_player_id"]
        client = ada if active == state["characters"][active]["player_id"] and \
            state["you"] and state["you"]["player_id"] == active else ben
        response = client.post(f"/api/rooms/{room_id}/action",
                               json={"ability_id": "unit_test_barrage"})
        if response.status_code != 200:
            client.post(f"/api/rooms/{room_id}/end-turn")
        # The table waits for the DM, so the DM has to actually write: without
        # draining the queue here every turn after the first is refused and
        # this stops being a test of a whole game.
        while worker.run_once(timeout=0.05):
            pass

    events = ada.get(f"/api/rooms/{room_id}/stream?once=1&since=0").get_data(as_text=True)
    assert "event: action" in events


def test_narration_reaches_the_event_log(rig):
    app, worker = rig
    ada = app.test_client()
    room_id = ada.post("/api/rooms", json={"name": "Kestrel",
                                           "archetype": "aircraft"}).get_json()["room_id"]
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ada.post(f"/api/rooms/{room_id}/start")
    ada.post(f"/api/rooms/{room_id}/action", json={"ability_id": "unit_test_barrage"})

    while worker.run_once(timeout=0.05):
        pass
    kinds = [e["kind"] for e in app.service.events_since(room_id, 0)]
    assert "narration" in kinds


def test_game_survives_a_process_restart(tmp_path):
    first = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                        "TESTING": True, "NARRATION": None})
    client = first.test_client()
    room_id = client.post("/api/rooms", json={"name": "Kestrel",
                                              "archetype": "aircraft"}).get_json()["room_id"]
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    client.post(f"/api/rooms/{room_id}/start")
    client.post(f"/api/rooms/{room_id}/action", json={"ability_id": "unit_test_barrage"})
    focus = client.get(f"/api/rooms/{room_id}/state").get_json()["you"]["focus"]

    second = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                         "TESTING": True, "NARRATION": None})
    state = second.test_client().get(f"/api/rooms/{room_id}/state").get_json()
    assert state["characters"][list(state["characters"])[0]]["focus"] == focus
    assert state["room"]["status"] == "active"


def test_lobby_lists_a_room_created_by_an_earlier_process(tmp_path):
    first = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                        "TESTING": True, "NARRATION": None})
    room_id = first.test_client().post(
        "/api/rooms", json={"name": "Kestrel",
                            "archetype": "aircraft"}).get_json()["room_id"]
    second = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                         "TESTING": True, "NARRATION": None})
    rooms = second.test_client().get("/api/rooms").get_json()["rooms"]
    assert room_id in {r["room_id"] for r in rooms}
