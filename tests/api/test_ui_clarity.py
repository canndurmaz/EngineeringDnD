"""The interface has to answer "what should I do right now?".

Three defects motivated these: an "Add bot" control that 403s before you have a
seat, ability buttons that are clickable in a room nobody has started, and no
sign at all that the DM is writing. There is no DOM here and no build step, so
these read the served source the way the rest of the frontend tests do.
"""
from tests.api.conftest import make_room


def js(client, name):
    return client.get(f"/static/js/{name}").get_data(as_text=True)


def css(client):
    return client.get("/static/css/theme.css").get_data(as_text=True)


def join_page(client, room_id=None):
    room_id = room_id or make_room(client)
    return client.get(f"/room/{room_id}/join").get_data(as_text=True)


def table_page(client, room_id=None):
    room_id = room_id or make_room(client)
    return client.get(f"/room/{room_id}").get_data(as_text=True)


# --- the annunciator --------------------------------------------------------

def test_the_table_carries_an_annunciator_band(client):
    body = table_page(client)
    assert 'id="annunciator"' in body
    band = body[body.index('id="annunciator"'):]
    band = band[:band.index("</div>")]
    assert 'class="lamp"' in band


def test_the_annunciator_announces_itself_to_a_screen_reader(client):
    body = table_page(client)
    band = body[body.index('id="annunciator"'):]
    assert 'aria-live="polite"' in band[:band.index("</div>")]


def test_the_annunciator_sits_above_the_story_log(client):
    """It is the first thing in the centre column, not a footnote under it."""
    body = table_page(client)
    assert body.index('id="annunciator"') < body.index('id="log"')


def test_the_annunciator_is_driven_by_the_room_status(client):
    body = js(client, "game.js")
    assert "renderAnnunciator" in body
    assert "state.room.status" in body
    for status in ('"lobby"', '"won"', '"active"'):
        assert status in body, status


def test_the_annunciator_offers_the_start_button_only_in_the_lobby(client):
    body = js(client, "game.js")
    assert "offerStart = !!state.you" in body
    assert 'el("ann-start").hidden = !offerStart' in body
    assert "/start" in body
    assert "Start the programme" in table_page(client)


def test_the_start_control_lives_on_the_table_not_only_the_join_page(client):
    """A seated player who walks to the table is told to start the programme;
    the control has to be there to be told about."""
    body = table_page(client)
    assert 'id="ann-start"' in body
    band = body[body.index('id="annunciator"'):body.index('id="log"')]
    assert 'id="ann-start"' in band
    # the same action carries the same name as the join page's button
    assert "Start the programme" in band
    assert "Start the programme" in js(client, "lobby.js")


def test_the_table_start_control_posts_to_the_same_endpoint(client):
    body = js(client, "game.js")
    handler = body[body.index('el("ann-start").addEventListener'):]
    handler = handler[:handler.index("\n});")]
    assert "/start" in handler and 'method: "POST"' in handler
    # the server's own refusal reaches the player rather than being swallowed
    assert 'el("ann-error").textContent = error.message' in handler


def test_a_spectator_in_the_lobby_gets_no_start_control(client):
    body = js(client, "game.js")
    assert "offerStart = !!state.you" in body
    assert 'el("ann-start").hidden = !offerStart' in body


def test_the_lobby_line_only_tells_a_seated_viewer_to_start(client):
    """A spectator cannot start (the server answers 403), so the band must not
    read as an instruction to them."""
    body = js(client, "game.js")
    lobby = body[body.index('if (status === "lobby")'):body.index('} else if (status === "won")')]
    assert "offerStart" in lobby
    assert "Start when everyone's in." in lobby
    assert "Waiting for a seated engineer to start." in lobby
    # the seated sentence is reached only through the offerStart branch
    assert lobby.index("offerStart") < lobby.index("Start when everyone's in.")


def test_the_spectator_line_is_not_duplicated_from_the_abilities_panel(client):
    """One sentence, one place: the band does not repeat the watching notice."""
    body = js(client, "game.js")
    assert body.count("You are watching this table") == 1


def test_the_start_control_sits_outside_the_live_region(client):
    """aria-live announces the state text; a button inside it would be
    re-announced on every poll."""
    body = table_page(client)
    live = body[body.index('id="ann-live"'):]
    live = live[:live.index("</p>")]
    assert 'id="ann-start"' not in live


def test_the_start_control_sits_quietly_inside_the_band(client):
    sheet = css(client)
    rule = sheet[sheet.index("#ann-start {"):]
    rule = rule[:rule.index("}")]
    assert "background: transparent" in rule
    assert "var(--lamp" in rule


def test_the_annunciator_names_whoever_the_table_is_waiting_on(client):
    body = js(client, "game.js")
    assert 'active.is_bot ? "working" : "deciding"' in body


def test_the_annunciator_reuses_the_pending_entry_as_the_writing_signal(client):
    """No new server state: the log already marks an action whose narration has
    not landed."""
    body = js(client, "game.js")
    assert 'narrationOutstanding' in body
    assert '.entry.pending' in body
    assert "dm writing" in body


def test_the_annunciator_says_how_the_programme_ended(client):
    body = js(client, "game.js")
    for status in ("lost_budget", "lost_schedule", "lost_burnout"):
        assert status in body, status
    assert "The budget ran out." in body


def test_the_annunciator_text_is_set_as_text_not_markup(client):
    """Room and character names reach this band; none of it is interpolated
    into innerHTML."""
    body = js(client, "game.js")
    assert 'el("ann-word").textContent' in body
    assert 'el("ann-say").textContent' in body


def test_only_the_lamp_moves_and_only_while_waiting(client):
    sheet = css(client)
    assert "lamp-pulse" in sheet
    assert '.annunciator[data-state="you"] .lamp' in sheet
    assert '.annunciator[data-state="writing"] .lamp' in sheet
    # the settled states do not animate
    for state in ("lost", "won"):
        assert f'.annunciator[data-state="{state}"] .lamp {{\n  animation' not in sheet


def test_a_reduced_motion_reader_gets_a_still_lamp(client):
    sheet = css(client)
    block = sheet[sheet.index("@media (prefers-reduced-motion: reduce)"):]
    block = block[:block.index("}\n\n")]
    assert ".annunciator .lamp" in block and "animation: none" in block


def test_the_annunciator_stacks_on_a_phone(client):
    sheet = css(client)
    assert "@media (max-width: 520px)" in sheet


def test_every_control_shows_the_keyboard(client):
    assert ":focus-visible" in css(client)


# --- gating the controls on real state --------------------------------------

def test_ability_buttons_are_dead_until_the_programme_starts(client):
    """room.status decides, not the player's own ability list."""
    body = js(client, "game.js")
    assert 'state.room.status === "active"' in body
    assert 'const lobby = state.room.status === "lobby"' in body
    assert 'if (lobby) why = "the programme hasn\'t started"' in body
    assert 'why ? "disabled" : ""' in body


def test_the_gate_requires_a_seat_and_the_turn_as_well(client):
    body = js(client, "game.js")
    gate = body[body.index("const canAct ="):]
    gate = gate[:gate.index(";")]
    assert 'state.room.status === "active"' in gate
    assert "!!state.you" in gate
    assert "state.turn.active_player_id === state.you.player_id" in gate


def test_the_pass_button_takes_the_same_gate(client):
    body = js(client, "game.js")
    assert "const mine = canAct(state);" in body
    assert 'el("pass").disabled = !mine;' in body


def test_the_existing_why_text_survives(client):
    body = js(client, "game.js")
    assert "needs ${a.focus_cost} focus, you have ${me.focus}" in body
    assert '"not your turn"' in body
    assert '"you are burned out"' in body


def test_a_spectator_is_told_they_are_watching(client):
    body = js(client, "game.js")
    assert "You are watching this table" in body
    assert ".watching" in css(client)


def test_the_server_still_refuses_an_action_in_a_lobby_room(client):
    """The gate is a courtesy; the refusal is the contract."""
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    response = client.post(f"/api/rooms/{room_id}/action",
                           json={"ability_id": "unit_test_barrage"})
    assert response.status_code == 400
    assert "not started" in response.get_json()["error"]


# --- the add-bot dead end ---------------------------------------------------

def test_the_join_page_offers_no_bot_control_before_the_visitor_is_seated(client):
    body = join_page(client)
    assert "Add bot" not in body
    assert "bot-add" not in body


def test_the_join_page_says_to_take_a_seat_first(client):
    body = join_page(client)
    assert "Pick your discipline first" in body
    assert "you can add bots to the empty seats afterwards" in body
    assert body.index("seat-first") < body.index('id="classes"')


def test_the_bot_control_is_drawn_only_once_this_visitor_holds_a_seat(client):
    body = js(client, "lobby.js")
    assert "joined = !!state.you;" in body
    assert "${joined && !isTaken ?" in body


def test_an_unclaimed_card_reads_take_this_seat_before_joining(client):
    body = js(client, "lobby.js")
    assert 'joined ? "Open seat" : "Take this seat"' in body


def test_the_server_refuses_a_bot_from_an_unseated_visitor(client):
    """The 403 the player actually hit."""
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/bots",
                           json={"class_id": "computer_scientist"})
    assert response.status_code == 403
    assert response.get_json()["error"]


def test_a_bot_is_welcome_once_the_visitor_has_a_seat(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    response = client.post(f"/api/rooms/{room_id}/bots",
                           json={"class_id": "mechanical_engineer"})
    assert response.status_code == 201


def test_a_failed_bot_call_surfaces_the_servers_own_message(client):
    """Not a shrug: whatever the server said lands in the error element, and it
    survives the redraw that follows."""
    body = js(client, "lobby.js")
    add = body[body.index("/rooms/${roomId}/bots`"):]
    add = add[:add.index("return;")]
    assert "failed = error.message" in add
    assert 'show("join-error", failed)' in add
    assert add.index("await refresh()") < add.index('show("join-error", failed)')


def test_the_error_element_is_outside_the_form_that_gets_hidden(client):
    """The form is hidden the moment a player is seated; an error inside it
    would be invisible exactly when bots are being added."""
    body = join_page(client)
    assert body.index("</form>") < body.index('id="join-error"')


def test_a_seated_player_can_still_reach_the_table(client):
    body = join_page(client)
    assert 'id="to-table"' in body
    assert "Go to the table" in body


# --- the audit: every control on the table gated on room state and seat ------

def test_the_pass_button_is_dead_for_a_spectator(client):
    """canAct() requires a seat, and the pass button takes canAct()."""
    body = js(client, "game.js")
    gate = body[body.index("const canAct ="):]
    gate = gate[:gate.index(";")]
    assert "!!state.you" in gate
    assert 'el("pass").disabled = !mine;' in body
    # and the disabling happens before the spectator early-return
    render = body[body.index("function renderAbilities"):]
    render = render[:render.index("\n}")]
    assert render.index('el("pass").disabled') < render.index("if (!me)")


def test_a_spectator_gets_no_ability_buttons_at_all(client):
    body = js(client, "game.js")
    render = body[body.index("function renderAbilities"):]
    render = render[:render.index("\n}")]
    spectator = render[render.index("if (!me) {"):]
    assert "return;" in spectator[:spectator.index("me.abilities")]


def test_the_level_up_picker_is_hidden_without_a_seat(client):
    """/level-choice answers 403 to anyone unseated, so the row is not drawn."""
    body = js(client, "game.js")
    render = body[body.index("function renderLevelChoice"):]
    render = render[:render.index("\n}")]
    assert 'if (!state.you || !STATS.length) { box.innerHTML = ""; return; }' in render


def test_the_server_refuses_a_level_choice_from_an_unseated_visitor(client):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/level-choice", json={"stat": "RIGOR"})
    assert response.status_code == 403


def test_the_server_refuses_a_start_from_an_unseated_visitor(client):
    """The 403 behind the spectator's missing button."""
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    with client.session_transaction() as session:
        session.clear()                      # same room, no seat
    response = client.post(f"/api/rooms/{room_id}/start")
    assert response.status_code == 403
    assert "not seated" in response.get_json()["error"]


def test_the_table_page_carries_no_ungated_bot_control(client):
    """Bots are added from the join page, where the seat gate already lives."""
    body = table_page(client)
    assert "bot-add" not in body
    assert "Add bot" not in body
