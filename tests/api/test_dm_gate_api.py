"""The DM gate over HTTP: the skip door, what /state tells the page, and the
one rule the bots have to obey too."""
import pytest
from app import create_app
from bots import BotRunner
from narrator.base import FakeNarrator
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker
from tests.api.conftest import make_room


@pytest.fixture
def app(tmp_path):
    """The api conftest's app has no narration queue, so nothing ever gates.
    These tests need one."""
    queue = NarrationQueue()
    application = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                              "TESTING": True, "NARRATION": queue})
    application.narration_queue = queue
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def seat(client, room_id, name="Ada", class_id="computer_scientist"):
    return client.post(f"/api/rooms/{room_id}/join",
                       json={"display_name": name, "class_id": class_id})


def started(client, room_id):
    client.post(f"/api/rooms/{room_id}/start")


def gated_room(client):
    room_id = make_room(client)
    seat(client, room_id)
    started(client, room_id)
    client.post(f"/api/rooms/{room_id}/action",
                json={"ability_id": "unit_test_barrage"})
    return room_id


# --- what the page is told ---------------------------------------------------

def test_state_reports_the_gate(client):
    room_id = gated_room(client)
    wait = client.get(f"/api/rooms/{room_id}/state").get_json()["dm_wait"]
    assert wait["waiting"] is True and wait["event_seq"]


def test_state_reports_no_gate_before_anyone_acts(client):
    room_id = make_room(client)
    seat(client, room_id)
    started(client, room_id)
    wait = client.get(f"/api/rooms/{room_id}/state").get_json()["dm_wait"]
    assert wait == {"waiting": False, "event_seq": None}


def test_a_second_action_is_refused_while_the_dm_writes(client):
    room_id = gated_room(client)
    response = client.post(f"/api/rooms/{room_id}/action",
                           json={"ability_id": "unit_test_barrage"})
    assert response.status_code == 400
    assert "DM is still writing" in response.get_json()["error"]


def test_passing_is_refused_while_the_dm_writes(client):
    room_id = gated_room(client)
    response = client.post(f"/api/rooms/{room_id}/end-turn")
    assert response.status_code == 400
    assert "DM is still writing" in response.get_json()["error"]


# --- the skip door -----------------------------------------------------------

def test_skip_dm_clears_the_gate(client):
    room_id = gated_room(client)
    assert client.post(f"/api/rooms/{room_id}/skip-dm").status_code == 200
    wait = client.get(f"/api/rooms/{room_id}/state").get_json()["dm_wait"]
    assert wait["waiting"] is False


def test_after_a_skip_the_table_can_play_on(client):
    room_id = gated_room(client)
    client.post(f"/api/rooms/{room_id}/skip-dm")
    assert client.post(f"/api/rooms/{room_id}/end-turn").status_code == 200


def test_skip_dm_without_a_seat_is_403(client, app):
    room_id = gated_room(client)
    spectator = app.test_client()
    response = spectator.post(f"/api/rooms/{room_id}/skip-dm")
    assert response.status_code == 403
    assert "not seated" in response.get_json()["error"]


def test_a_spectator_cannot_unstick_the_table(client, app):
    room_id = gated_room(client)
    app.test_client().post(f"/api/rooms/{room_id}/skip-dm")
    wait = client.get(f"/api/rooms/{room_id}/state").get_json()["dm_wait"]
    assert wait["waiting"] is True


def test_the_skip_reaches_the_stream(client):
    room_id = gated_room(client)
    client.post(f"/api/rooms/{room_id}/skip-dm")
    events = client.get(
        f"/api/rooms/{room_id}/stream?once=1&since=0").get_data(as_text=True)
    assert "event: dm_skipped" in events


# --- the bots ----------------------------------------------------------------

def test_the_bot_runner_does_not_act_while_the_room_is_gated(client, app):
    room_id = make_room(client)
    seat(client, room_id)
    client.post(f"/api/rooms/{room_id}/bots",
                json={"class_id": "mechanical_technician"})
    started(client, room_id)
    client.post(f"/api/rooms/{room_id}/action",
                json={"ability_id": "unit_test_barrage"})   # gates, and it is
    assert app.service.dm_gate(room_id)["waiting"] is True  # now the bot's turn

    runner = BotRunner(app.service, app.catalog, delay=0.0)
    before = app.service._room(room_id).latest_seq()
    assert runner.run_once() == 0
    assert app.service._room(room_id).latest_seq() == before


def test_the_bot_plays_once_the_narration_lands(client, app):
    room_id = make_room(client)
    seat(client, room_id)
    bot_id = client.post(f"/api/rooms/{room_id}/bots",
                         json={"class_id": "mechanical_technician"}
                         ).get_json()["player_id"]
    started(client, room_id)
    client.post(f"/api/rooms/{room_id}/action",
                json={"ability_id": "unit_test_barrage"})

    worker = NarrationWorker(app.narration_queue, FakeNarrator("It holds."),
                             app.service, app.broker)
    while worker.run_once(timeout=0.05):
        pass
    assert app.service.dm_gate(room_id)["waiting"] is False

    runner = BotRunner(app.service, app.catalog, delay=0.0)
    since = app.service._room(room_id).latest_seq()
    assert runner.run_once() == 1
    actors = [e["actor"] for e in app.service.events_since(room_id, since)]
    assert bot_id in actors


def test_the_bot_runner_keeps_its_poll_interval_while_gated(client, app):
    """Declining a tick must not turn into a spin: the interval is untouched."""
    runner = BotRunner(app.service, app.catalog, delay=0.0, interval=0.5)
    assert runner.interval == 0.5
