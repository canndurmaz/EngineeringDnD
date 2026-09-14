"""The character-select pickers and the avatars in the party panels.

There is no JS runtime here, so these assert the wiring a reader would
otherwise have to re-check by hand: that the markup is present, that it points
at the local endpoint, and that every avatar URL is both escaped and
URL-encoded. A stored XSS shipped in this file's neighbour once already.
"""
import pathlib

from tests.api.conftest import make_room

LOBBY_JS = pathlib.Path("static/js/lobby.js").read_text()
GAME_JS = pathlib.Path("static/js/game.js").read_text()


def test_join_page_has_a_preview_and_a_randomise_button(client):
    room_id = make_room(client)
    body = client.get(f"/room/{room_id}/join").get_data(as_text=True)
    assert 'id="avatar-preview"' in body
    assert 'id="pickers"' in body
    assert 'id="randomise"' in body


def test_join_page_still_references_no_external_hosts(client):
    room_id = make_room(client)
    body = client.get(f"/room/{room_id}/join").get_data(as_text=True)
    assert "http://" not in body.replace("http://www.w3.org", "")
    assert "https://" not in body


def test_both_scripts_build_the_avatar_url_from_the_local_endpoint():
    for source in (LOBBY_JS, GAME_JS):
        assert '"/api/avatar.svg?"' in source


def test_every_avatar_url_is_url_encoded_and_escaped():
    for source in (LOBBY_JS, GAME_JS):
        assert source.count("encodeURIComponent") >= 2
        assert "esc(avatarUrl(" in source


def test_the_join_request_sends_the_chosen_appearance():
    assert "appearance: look" in LOBBY_JS


def test_the_picker_label_is_written_as_text_not_markup():
    """A server-served label is trusted, but textContent costs nothing."""
    assert "textContent = labelFor(kind)" in LOBBY_JS


def test_the_party_panel_renders_an_avatar():
    assert 'class="avatar"' in GAME_JS


def test_the_theme_styles_the_avatar_from_existing_custom_properties():
    css = pathlib.Path("static/css/theme.css").read_text()
    assert ".avatar {" in css
    assert ".avatar-studio" in css
    avatar_block = css[css.index("/* --- avatars"):]
    # No new palette: every colour in the new rules is an existing token.
    assert "#" not in avatar_block, "the avatar styles introduced a raw colour"
