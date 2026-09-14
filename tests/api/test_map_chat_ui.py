"""The two new panels, read off the served source.

There is no DOM here and no build step, so these read the shipped files the way
the rest of the frontend tests do.
"""
import re

from tests.api.conftest import make_room


def js(client, name="game.js"):
    return client.get(f"/static/js/{name}").get_data(as_text=True)


def css(client):
    return client.get("/static/css/theme.css").get_data(as_text=True)


def table(client):
    room_id = make_room(client)
    return client.get(f"/room/{room_id}").get_data(as_text=True)


# --- the schematic ----------------------------------------------------------

def test_the_table_has_a_map_panel_above_the_rail(client):
    body = table(client)
    assert 'id="schematic"' in body
    assert body.index('id="schematic"') < body.index('id="rail"')
    assert body.index('id="schematic"') < body.index('id="hazard"')


def test_the_map_is_inline_svg_with_no_image_file_and_no_library(client):
    body = js(client)
    assert "<svg" in body
    assert "viewBox" in body
    assert ".svg\"" not in body[body.index("function renderMap"):
                                body.index("/* --- table talk")]


def test_the_layout_is_a_fixed_grid_so_nodes_do_not_jump(client):
    """Node i is always at the same cell: position is a function of the index,
    never of the data."""
    body = js(client)
    assert "MAP_COLS" in body
    assert "(i % MAP_COLS) * (MAP_W + MAP_GX)" in body
    assert "Math.floor(i / MAP_COLS) * (MAP_H + MAP_GY)" in body


def test_the_four_node_states_each_have_a_rule(client):
    sheet = css(client)
    for state in ("clear", "open", "done", "active"):
        assert f".node.{state} rect" in sheet


def test_only_the_active_node_moves(client):
    sheet = css(client)
    block = sheet[sheet.index("/* --- the system map"):
                  sheet.index("/* --- table talk")]
    assert block.count("animation:") == 1
    assert ".node.active rect" in block[:block.index("animation:")]


def test_the_node_pulse_is_suppressed_under_reduced_motion(client):
    sheet = css(client)
    reduced = sheet[sheet.index("@media (prefers-reduced-motion: reduce)"):]
    reduced = reduced[:reduced.index("\n}")]
    assert ".node.active rect { animation: none; }" in reduced


def test_the_map_stays_legible_on_a_phone(client):
    """It scales with its column instead of overflowing it."""
    sheet = css(client)
    assert ".schematic-svg {" in sheet
    block = sheet[sheet.index(".schematic-svg {"):]
    block = block[:block.index("}")]
    assert "width: 100%" in block
    assert "height: auto" in block


def test_every_node_label_and_tooltip_is_escaped(client):
    body = js(client)
    block = body[body.index("function renderMap"):body.index("/* --- table talk")]
    assert "esc(line)" in block
    assert "esc(n.status)" in block
    assert "esc(n.name)" in block and "esc(n.blurb)" in block


def test_the_active_hazard_name_goes_under_the_schematic_as_text(client):
    body = js(client)
    assert 'id="map-active"' in table(client)
    assert "caption.textContent" in body


# --- table talk -------------------------------------------------------------

def test_the_chat_panel_sits_under_the_party_list(client):
    body = table(client)
    assert body.index('id="party-panel"') < body.index('id="chat-panel"')
    assert 'id="chat-log"' in body and 'id="chat-input"' in body
    assert 'id="chat-send"' in body


def test_a_chat_body_is_escaped_before_it_reaches_the_dom(client):
    """The highest-risk string in the feature: player-typed, rendered for
    everyone. This codebase has had a stored XSS once."""
    body = js(client)
    assert "esc(message.body)" in body
    assert "esc(message.name)" in body
    block = body[body.index("function appendChat"):body.index("function showChat")]
    assert "${message.body}" not in block
    assert "${message.name}" not in block


def test_a_bot_is_marked_with_the_existing_chip(client):
    assert 'chip-bot">BOT' in js(client)


def test_enter_sends_because_the_input_is_a_form(client):
    body = table(client)
    assert '<form id="chat-form"' in body
    assert 'el("chat-form").addEventListener("submit"' in js(client)


def test_typing_in_the_chat_cannot_reach_a_document_level_shortcut(client):
    body = js(client)
    assert 'el("chat-input").addEventListener("keydown"' in body
    assert "event.stopPropagation()" in body


def test_a_spectator_is_told_why_they_cannot_talk(client):
    body = js(client)
    assert "Take a seat to talk at this table." in body
    block = body[body.index("function renderChatSeat"):]
    assert "input.disabled = !seated" in block
    assert "send.disabled = !seated" in block


def test_the_client_listens_for_chat_on_the_stream(client):
    assert '"chat"]' in js(client)


def test_the_sender_name_is_the_cyan_accent_and_the_body_the_prose_face(client):
    sheet = css(client)
    who = sheet[sheet.index(".chat-who {"):]
    assert "var(--cyan)" in who[:who.index("}")]
    prose = sheet[sheet.index(".chat-body {"):]
    assert "var(--sans)" in prose[:prose.index("}")]


def test_the_new_panels_introduce_no_raw_colours(client):
    """The identity is the existing custom properties, extended, not redesigned.

    rgba() tints of an existing token are the established way this sheet fills a
    shape (see the crit and fumble flashes); a hex literal is a new colour.
    """
    sheet = css(client)
    block = sheet[sheet.index("/* --- the system map"):
                  sheet.index("/* --- avatars")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block), (
        "the map or chat styles introduced a raw colour")
    assert block.count("var(--") >= 10


def test_no_page_reaches_for_an_external_host(client):
    """Already asserted for the pages; re-asserted for the two new panels."""
    for body in (js(client), css(client), table(client)):
        assert "https://" not in body
        assert "http://" not in body.replace("http://www.w3.org", "")
