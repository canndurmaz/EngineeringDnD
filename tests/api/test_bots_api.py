"""Seating bots over HTTP, and the runner that plays them."""
from bots import BotRunner
from tests.api.conftest import make_room


def seat(client, room_id, name="Ada", class_id="computer_scientist"):
    """Join as a human; the session cookie this sets is the seat guard."""
    return client.post(f"/api/rooms/{room_id}/join",
                       json={"display_name": name, "class_id": class_id})


def add_bot(client, room_id, class_id="mechanical_technician"):
    return client.post(f"/api/rooms/{room_id}/bots", json={"class_id": class_id})


# --- the endpoints ----------------------------------------------------------

def test_a_seated_player_can_add_a_bot(client):
    room_id = make_room(client)
    seat(client, room_id)
    response = add_bot(client, room_id)
    assert response.status_code == 201
    character = response.get_json()["character"]
    assert character["is_bot"] is True
    assert character["name"].startswith("Unit-")
    assert character["appearance"]                  # bots get a face like anyone


def test_adding_a_bot_needs_a_seat(client):
    """Same guard as /start: only people at the table may fill it."""
    room_id = make_room(client)
    assert add_bot(client, room_id).status_code == 403


def test_a_bot_cannot_take_a_class_someone_already_has(client):
    room_id = make_room(client)
    seat(client, room_id, class_id="computer_scientist")
    response = add_bot(client, room_id, class_id="computer_scientist")
    assert response.status_code == 400
    assert "already taken" in response.get_json()["error"]


def test_a_bot_cannot_take_a_class_another_bot_has(client):
    room_id = make_room(client)
    seat(client, room_id)
    add_bot(client, room_id, class_id="ee_engineer")
    assert add_bot(client, room_id, class_id="ee_engineer").status_code == 400


def test_an_unknown_class_is_rejected(client):
    room_id = make_room(client)
    seat(client, room_id)
    assert add_bot(client, room_id, class_id="astrologer").status_code == 400


def test_the_room_fills_up(client):
    room_id = make_room(client)
    seat(client, room_id, class_id="computer_scientist")
    for class_id in ("mechanical_engineer", "ee_engineer", "system_engineer",
                     "product_manager", "mechanical_technician"):
        assert add_bot(client, room_id, class_id=class_id).status_code == 201
    response = add_bot(client, room_id, class_id="electrical_technician")
    assert response.status_code == 400
    assert "full" in response.get_json()["error"]


def test_a_bot_can_be_removed(client):
    room_id = make_room(client)
    seat(client, room_id)
    bot_id = add_bot(client, room_id).get_json()["player_id"]
    assert client.delete(f"/api/rooms/{room_id}/bots/{bot_id}").status_code == 200
    state = client.get(f"/api/rooms/{room_id}/state").get_json()
    assert bot_id not in state["characters"]
    assert bot_id not in state["turn"]["order"]


def test_a_human_cannot_be_deleted_through_the_bot_endpoint(client):
    room_id = make_room(client)
    human_id = seat(client, room_id).get_json()["player_id"]
    add_bot(client, room_id)                        # someone has to hold the seat
    response = client.delete(f"/api/rooms/{room_id}/bots/{human_id}")
    assert response.status_code == 400
    assert "only a bot" in response.get_json()["error"]
    assert human_id in client.get(
        f"/api/rooms/{room_id}/state").get_json()["characters"]


def test_removing_a_bot_needs_a_seat(client):
    room_id = make_room(client)
    seat(client, room_id)
    bot_id = add_bot(client, room_id).get_json()["player_id"]
    with client.session_transaction() as session:
        session.clear()
    assert client.delete(f"/api/rooms/{room_id}/bots/{bot_id}").status_code == 403


def test_removing_someone_who_is_not_there(client):
    room_id = make_room(client)
    seat(client, room_id)
    assert client.delete(f"/api/rooms/{room_id}/bots/nobody").status_code == 400


# --- party counts in the payload -------------------------------------------

def test_the_state_payload_counts_the_party(client):
    room_id = make_room(client)
    seat(client, room_id)
    add_bot(client, room_id, class_id="ee_engineer")
    add_bot(client, room_id, class_id="system_engineer")
    size = client.get(f"/api/rooms/{room_id}/state").get_json()["party_size"]
    assert size["seated"] == 3
    assert size["bots"] == 2
    assert size["humans"] == 1
    assert size["max"] >= 3


def test_an_empty_room_counts_nobody(client):
    room_id = make_room(client)
    size = client.get(f"/api/rooms/{room_id}/state").get_json()["party_size"]
    assert size == {"seated": 0, "bots": 0, "humans": 0, "max": size["max"]}


def test_the_lobby_listing_carries_the_bot_count(client):
    room_id = make_room(client)
    seat(client, room_id)
    add_bot(client, room_id)
    room = next(r for r in client.get("/api/rooms").get_json()["rooms"]
                if r["room_id"] == room_id)
    assert room["player_count"] == 2 and room["bot_count"] == 1


# --- the runner -------------------------------------------------------------

def _runner(app):
    """No thread, no waiting: run_once is the whole loop body."""
    return BotRunner(app.service, app.catalog, delay=0.0)


def _started(client, app):
    room_id = make_room(client)
    seat(client, room_id)
    bot_id = add_bot(client, room_id).get_json()["player_id"]
    client.post(f"/api/rooms/{room_id}/start")
    return room_id, bot_id


def test_run_once_does_nothing_while_a_human_is_up(client, app):
    room_id, _ = _started(client, app)
    before = app.service._room(room_id).latest_seq()
    assert _runner(app).run_once() == 0
    assert app.service._room(room_id).latest_seq() == before


def test_run_once_plays_the_bots_turn(client, app):
    room_id, bot_id = _started(client, app)
    client.post(f"/api/rooms/{room_id}/end-turn")     # hand over to the bot
    state = app.service.snapshot(room_id)
    assert state["turn"]["order"][state["turn"]["turn_index"]] == bot_id

    since = app.service._room(room_id).latest_seq()
    assert _runner(app).run_once() == 1
    kinds = [(e["kind"], e["actor"])
             for e in app.service.events_since(room_id, since)]
    # An office place action (the lab bench, the coffee machine) is a turn too.
    assert (("action", bot_id) in kinds) or (("passed", bot_id) in kinds) \
        or (("place_action", bot_id) in kinds)
    after = app.service.snapshot(room_id)
    assert after["turn"]["order"][after["turn"]["turn_index"]] != bot_id


def test_the_runner_waits_its_beat_before_acting(client, app):
    """A bot answering in the same millisecond reads as a glitch, not a move."""
    room_id, _ = _started(client, app)
    client.post(f"/api/rooms/{room_id}/end-turn")
    runner = BotRunner(app.service, app.catalog, delay=2.0)
    assert runner.run_once(now=100.0) == 0            # first sighting: wait
    assert runner.run_once(now=101.0) == 0            # still inside the beat
    assert runner.run_once(now=102.5) == 1


def test_the_runner_leaves_a_room_that_has_not_started(client, app):
    room_id = make_room(client)
    seat(client, room_id)
    add_bot(client, room_id)
    before = app.service._room(room_id).latest_seq()
    assert _runner(app).run_once() == 0
    assert app.service._room(room_id).latest_seq() == before


def test_one_broken_room_does_not_stop_the_sweep(client, app):
    """The narration worker swallows a bad job; so must this one."""
    room_id, bot_id = _started(client, app)
    client.post(f"/api/rooms/{room_id}/end-turn")
    runner = _runner(app)
    real = app.service.snapshot

    def explode(rid):
        if rid == "ghost":
            raise RuntimeError("this room is on fire")
        return real(rid)

    app.service.snapshot = explode
    try:
        app.service.index.upsert("ghost", "Ghost", "aircraft", 0, "active", 1)
        assert runner.run_once() == 1        # the healthy room still played
    finally:
        app.service.snapshot = real


def test_the_bot_delay_is_configurable_by_environment(monkeypatch, app):
    monkeypatch.setenv("CP_BOT_DELAY", "0.25")
    assert BotRunner(app.service, app.catalog).delay == 0.25


def test_a_nonsense_bot_delay_falls_back_to_the_default(monkeypatch, app):
    monkeypatch.setenv("CP_BOT_DELAY", "soon")
    assert BotRunner(app.service, app.catalog).delay == 2.0
