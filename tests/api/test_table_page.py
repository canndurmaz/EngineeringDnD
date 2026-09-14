from tests.api.conftest import make_room


def seat(client, room_id, name="Ada", class_id="computer_scientist"):
    return client.post(f"/api/rooms/{room_id}/join",
                       json={"display_name": name, "class_id": class_id})


def test_table_has_the_three_regions(client):
    room_id = make_room(client)
    body = client.get(f"/room/{room_id}").get_data(as_text=True)
    for anchor in ('id="party"', 'id="log"', 'id="rail"', 'id="hazard"',
                   'id="abilities"'):
        assert anchor in body


def test_table_loads_the_game_script(client):
    room_id = make_room(client)
    assert "js/game.js" in client.get(f"/room/{room_id}").get_data(as_text=True)


def test_table_exposes_the_room_id_to_javascript(client):
    room_id = make_room(client)
    body = client.get(f"/room/{room_id}").get_data(as_text=True)
    assert "window.ROOM_ID" in body and room_id in body


def test_game_script_is_served(client):
    response = client.get("/static/js/game.js")
    assert response.status_code == 200
    assert b"EventSource" in response.data


def test_game_script_reconnects_with_last_event_id(client):
    body = client.get("/static/js/game.js").get_data(as_text=True)
    assert "since=" in body


def test_table_shows_the_narrator_badge(client):
    room_id = make_room(client)
    assert 'id="dm-badge"' in client.get(f"/room/{room_id}").get_data(as_text=True)


def test_game_script_subscribes_to_the_phase_interlude(client):
    """narrator/worker publishes `interlude`; a kind the client does not
    addEventListener for is silently dropped by EventSource."""
    body = client.get("/static/js/game.js").get_data(as_text=True)
    assert '"interlude"' in body
    assert 'event.kind === "interlude"' in body


# --- "+1 to a stat of choice" on level-up ----------------------------------

def test_table_has_a_level_choice_row(client):
    room_id = make_room(client)
    assert 'id="level-choice"' in client.get(f"/room/{room_id}").get_data(as_text=True)


def test_game_script_posts_the_level_choice(client):
    body = client.get("/static/js/game.js").get_data(as_text=True)
    assert "/level-choice" in body
    assert '"stat": stat' in body or "{ stat }" in body


def test_game_script_reads_the_stat_names_from_the_api(client):
    """The six stats are served by /api/classes; the client must not keep its
    own copy that can drift from engine.classes.STATS."""
    body = client.get("/static/js/game.js").get_data(as_text=True)
    assert "/api/classes" in body
    stats = client.get("/api/classes").get_json()["stats"]
    for stat in stats:
        assert f'"{stat}"' not in body, f"{stat} is hard-coded in game.js"


def test_the_level_choice_buttons_escape_their_labels(client):
    body = client.get("/static/js/game.js").get_data(as_text=True)
    assert 'data-stat="${esc(stat)}"' in body
    assert "esc(current)" in body


def test_api_classes_still_serves_six_stats(client):
    assert len(client.get("/api/classes").get_json()["stats"]) == 6
