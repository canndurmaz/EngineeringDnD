"""The Office tab, read off the served source (no DOM, no build step)."""
import re

from tests.api.conftest import make_room


def page(client):
    room_id = make_room(client)
    return client.get(f"/room/{room_id}").get_data(as_text=True)


def office_js(client):
    return client.get("/static/js/office.js").get_data(as_text=True)


def game_js(client):
    return client.get("/static/js/game.js").get_data(as_text=True)


def test_office_js_is_served_and_reaches_no_external_host(client):
    body = office_js(client)
    assert "Phaser" in body
    assert "https://" not in body
    assert "http://" not in body.replace("http://www.w3.org", "")


def test_the_table_page_loads_phaser_from_this_server(client):
    body = page(client)
    assert "/static/vendor/phaser.min.js" in body
    assert "/static/js/office.js" in body
    assert "cdn" not in body.lower()
    # Phaser first, then the scene, then the game that calls into it.
    assert body.index("phaser.min.js") < body.index("office.js") < body.index("game.js")


def test_the_page_has_board_and_office_tabs(client):
    body = page(client)
    assert 'role="tablist"' in body
    assert 'id="tab-board"' in body and 'id="tab-office"' in body
    assert re.search(r'id="view-office"[^>]*hidden', body)
    assert 'id="office-canvas"' in body
    assert 'id="desk-panel"' in body


def test_the_board_is_unchanged_inside_its_tab(client):
    body = page(client)
    assert body.index('id="view-board"') < body.index('class="table-grid"') \
        < body.index('id="view-office"')


def test_keys_are_read_only_from_the_focused_canvas(client):
    """Typing in the chat (or any field) must never walk the avatar."""
    body = office_js(client)
    assert 'box.addEventListener("keydown"' in body
    assert "document.addEventListener(\"keydown\"" not in body
    assert "window.addEventListener(\"keydown\"" not in body
    assert "keyboard: false" in body                 # Phaser's global capture off
    assert "INPUT|TEXTAREA|SELECT" in body
    # and the chat input still swallows its own keys
    assert 'el("chat-input").addEventListener("keydown", (event) => event.stopPropagation())' \
        in game_js(client)


def test_server_text_never_reaches_inner_html_in_the_office(client):
    body = office_js(client)
    assert "innerHTML" not in body
    assert "insertAdjacentHTML" not in body


def test_the_place_log_line_escapes_everything(client):
    body = game_js(client)
    block = body[body.index("function placeLine"):body.index("const logLine")]
    for value in ("who", "event.action_name || event.action",
                  "lastState.characters[event.partner_id].name"):
        assert f"${{esc({value})}}" in block


def test_the_stream_carries_the_office_events(client):
    body = game_js(client)
    for kind in ("office_move", "place_action", "desk_updated"):
        assert f'"{kind}"' in body


def test_reduced_motion_snaps_instead_of_walking(client):
    body = office_js(client)
    assert "prefers-reduced-motion: reduce" in body
    assert "REDUCED" in body[body.index("sync(first)"):]


def test_touch_and_click_walk(client):
    body = office_js(client)
    assert 'this.input.on("pointerdown"' in body


def test_the_canvas_scales_to_its_container(client):
    body = office_js(client)
    assert "Scale.FIT" in body


def test_the_office_styles_use_the_existing_tokens(client):
    sheet = client.get("/static/css/theme.css").get_data(as_text=True)
    block = sheet[sheet.index("/* --- the office"):]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block)
    assert block.count("var(--") >= 10
