"""What the table shows while the DM is writing.

Same shape as the rest of the frontend tests: no DOM, no build step, so these
read the served source and pin the decisions rather than the pixels.
"""
from tests.api.conftest import make_room


def js(client, name):
    return client.get(f"/static/js/{name}").get_data(as_text=True)


def css(client):
    return client.get("/static/css/theme.css").get_data(as_text=True)


def table_page(client, room_id=None):
    room_id = room_id or make_room(client)
    return client.get(f"/room/{room_id}").get_data(as_text=True)


def test_the_page_reads_the_gate_from_the_server_not_from_itself(client):
    """Two browsers must never disagree about whether the table is paused."""
    body = js(client, "game.js")
    assert "state.dm_wait && state.dm_wait.waiting" in body


def test_the_band_shows_its_writing_state_while_gated(client):
    body = js(client, "game.js")
    branch = body[body.index("} else if (dmWaiting(state)) {"):]
    branch = branch[:branch.index("} else if (narrationOutstanding())")]
    assert 'lamp = "writing"' in branch
    assert '"dm writing"' in branch
    assert "offerSkip = true" in branch


def test_the_skip_control_lives_inside_the_band(client):
    body = table_page(client)
    band = body[body.index('id="annunciator"'):body.index('id="log"')]
    assert 'id="ann-skip"' in band
    assert "Skip the DM" in band


def test_the_skip_control_sits_outside_the_live_region(client):
    """aria-live announces the state text; a button inside it would be
    re-announced on every poll."""
    body = table_page(client)
    live = body[body.index('id="ann-live"'):]
    live = live[:live.index("</p>")]
    assert 'id="ann-skip"' not in live


def test_the_skip_control_is_offered_only_while_gated(client):
    body = js(client, "game.js")
    assert 'el("ann-skip").hidden = !(offerSkip && !!state.you);' in body
    assert "let lamp = " in body and "offerSkip = false" in body


def test_a_spectator_gets_no_skip_control(client):
    """/skip-dm answers 403 to anyone without a seat, so offering it would be
    an instruction they cannot carry out."""
    body = js(client, "game.js")
    assert "offerSkip && !!state.you" in body


def test_the_skip_control_posts_to_the_skip_endpoint(client):
    body = js(client, "game.js")
    handler = body[body.index('el("ann-skip").addEventListener'):]
    handler = handler[:handler.index("\n});")]
    assert "/skip-dm" in handler and 'method: "POST"' in handler
    assert 'el("ann-error").textContent = error.message' in handler


def test_the_skip_control_is_styled_as_quietly_as_the_start_control(client):
    sheet = css(client)
    rule = sheet[sheet.index("#ann-skip {"):]
    rule = rule[:rule.index("}")]
    assert "background: transparent" in rule
    assert "var(--lamp" in rule


def test_the_abilities_are_disabled_with_the_dm_as_the_reason(client):
    body = js(client, "game.js")
    assert 'const DM_WRITING_WHY = "the DM is writing";' in body
    assert "if (gated) why = DM_WRITING_WHY;" in body


def test_the_pass_button_is_disabled_while_gated(client):
    body = js(client, "game.js")
    assert 'if (gated) el("pass").disabled = true;' in body


def test_the_turn_hint_says_why_nothing_can_be_pressed(client):
    body = js(client, "game.js")
    assert "gated ? DM_WRITING_WHY" in body


def test_the_controls_re_enable_on_the_next_event(client):
    """Nothing polls a clock: every stream event calls refresh(), and refresh()
    re-reads the gate."""
    body = js(client, "game.js")
    assert 'if (event.kind !== "narration_chunk") refresh();' in body
    assert "renderAbilities(state)" in body


def test_the_skip_reaches_every_client_through_the_stream(client):
    body = js(client, "game.js")
    assert '"dm_skipped"' in body
    assert "dm_skipped:" in body          # and it says so in the log
