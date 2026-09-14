"""The three screens: adding a bot, marking one, counting the party, and the
d20 that settles before the rest of the line arrives.

There is no DOM here and no build step, so these read the served source the
same way the rest of the frontend tests do.
"""
from tests.api.conftest import make_room


def js(client, name):
    return client.get(f"/static/js/{name}").get_data(as_text=True)


def css(client):
    return client.get("/static/css/theme.css").get_data(as_text=True)


# --- adding and marking bots ------------------------------------------------

def test_character_select_offers_a_bot_for_an_unclaimed_class(client):
    body = js(client, "lobby.js")
    assert "Add bot" in body
    assert "/bots" in body


def test_the_bot_control_is_not_nested_inside_the_class_button(client):
    """A <button> inside a <button> never receives its own click."""
    body = js(client, "lobby.js")
    assert "pick-wrap" in body
    add = body[body.index('class="bot-add"'):]
    assert "</button>" in add[:add.index("Add bot")] or "data-bot" in add[:200]


def test_the_bot_class_id_is_escaped_and_url_encoded_in_the_attribute(client):
    """Same rule as every other attribute in this codebase."""
    body = js(client, "lobby.js")
    assert 'data-bot="${esc(encodeURIComponent(cls.id))}"' in body
    assert 'data-drop="${esc(encodeURIComponent(c.player_id))}"' in body


def test_the_party_panel_marks_bots(client):
    body = js(client, "game.js")
    assert "chip-bot" in body
    assert 'c.is_bot ?' in body
    assert ".chip-bot" in css(client)


def test_the_roster_marks_bots_too(client):
    assert "chip-bot" in js(client, "lobby.js")


# --- party counts -----------------------------------------------------------

def test_the_lobby_list_states_the_seated_count(client):
    body = js(client, "lobby.js")
    assert "seatedLabel(room.player_count, room.bot_count)" in body
    assert "${seated} seated" in body


def test_both_screens_state_the_bot_split(client):
    for name in ("lobby.js", "game.js"):
        assert "bot${bots === 1" in js(client, name), name


def test_character_select_has_a_party_heading(client):
    room_id = make_room(client)
    body = client.get(f"/room/{room_id}/join").get_data(as_text=True)
    assert 'id="party-count"' in body
    assert "Party:" in body


def test_the_table_party_panel_has_a_count_in_its_header(client):
    room_id = make_room(client)
    body = client.get(f"/room/{room_id}").get_data(as_text=True)
    header = body[body.index('id="party-panel"'):]
    assert 'id="party-count"' in header[:header.index("</h2>")]


def test_the_table_fills_the_count_from_the_payload(client):
    assert "state.party_size" in js(client, "game.js")


# --- the d20 animation ------------------------------------------------------

def test_the_roll_tumbles_on_a_class_toggled_from_javascript(client):
    body = js(client, "game.js")
    assert 'classList.add("rolling")' in body
    assert 'classList.remove("rolling")' in body
    assert "d20-tumble" in css(client)


def test_the_tumble_is_short(client):
    """About 600ms: long enough to read as a die, short enough to stay out of
    the way. The stylesheet and the timer must agree."""
    assert "const ROLL_MS = 600;" in js(client, "game.js")
    assert "animation: d20-tumble .6s" in css(client)


def test_a_reduced_motion_reader_gets_the_numbers_straight_away(client):
    body = css(client)
    block = body[body.index("@media (prefers-reduced-motion: reduce)"):]
    block = block[:block.index("}\n\n")]
    assert ".roll.rolling .nat" in block and "animation: none" in block
    assert ".roll.rolling .rest { opacity: 1; }" in block


def test_replayed_history_does_not_animate(client):
    """A client reconnecting receives every past action at once; forty dice
    tumbling together is noise."""
    body = js(client, "game.js")
    assert "isLive(event)" in body
    assert "replayUntil" in body
    assert "event.seq > replayUntil" in body


def test_the_natural_roll_still_settles_into_the_existing_pulses(client):
    body = js(client, "game.js")
    assert "flash-crit" in body and "flash-fumble" in body
    assert "pulse-green" in css(client) and "pulse-red" in css(client)
