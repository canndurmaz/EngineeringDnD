"""A gate boss's special rule, as the table is told about it.

A rule the player cannot read is indistinguishable from a bug: the move they
have made every turn suddenly does nothing and nothing on screen says why. So
the rule goes out with the hazard, unhidden, and the ability buttons repeat the
part of it that is about to cost them.
"""
from tests.api.conftest import make_room


def js(client, name="game.js"):
    return client.get(f"/static/js/{name}").get_data(as_text=True)


def at_the_boss(app, phase_gates=0):
    """A started room whose active hazard is the gate boss of its phase."""
    ada = app.test_client()
    room_id = make_room(ada)
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ada.post(f"/api/rooms/{room_id}/start")
    for _ in range(phase_gates):
        app.service._force_clear_phase(room_id)
    state = app.service.snapshot(room_id)
    while True:
        hazard = next(h for h in state["hazards"]
                      if h["id"] == state["active_hazard_id"])
        if hazard["is_boss"]:
            return room_id, ada
        app.service._force_defeat_active_hazard(room_id)
        state = app.service.snapshot(room_id)


def test_the_state_endpoint_serves_the_boss_rule(app):
    room_id, ada = at_the_boss(app)
    hazard = ada.get(f"/api/rooms/{room_id}/state").get_json()["hazard"]
    assert hazard["is_boss"]
    assert hazard["rule"]["id"] == "no_descope"
    assert hazard["rule"]["text"].strip()


def test_the_rule_is_visible_before_anything_is_revealed(app):
    """DC and weakness stay hidden; the rule never does."""
    room_id, ada = at_the_boss(app)
    hazard = ada.get(f"/api/rooms/{room_id}/state").get_json()["hazard"]
    assert hazard["dc"] is None and hazard["weakness"] is None
    assert hazard["rule"] is not None


def test_each_phase_gate_serves_its_own_rule(app):
    seen = []
    for gates in range(5):
        room_id, ada = at_the_boss(app, phase_gates=gates)
        hazard = ada.get(f"/api/rooms/{room_id}/state").get_json()["hazard"]
        seen.append(hazard["rule"]["id"])
    assert seen == ["no_descope", "rigor_only", "hands_on", "escalating",
                    "focused_fire"]


def test_an_ordinary_problem_serves_no_rule(app):
    ada = app.test_client()
    room_id = make_room(ada)
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ada.post(f"/api/rooms/{room_id}/start")
    hazard = ada.get(f"/api/rooms/{room_id}/state").get_json()["hazard"]
    assert not hazard["is_boss"] and hazard["rule"] is None


# --- the table ---------------------------------------------------------------

def test_the_hazard_card_renders_the_rule(client):
    source = js(client)
    assert "boss-rule" in source
    assert "esc(h.rule.text" in source


def test_the_rule_text_is_escaped_like_every_other_payload(client):
    """Same rule as every panel: nothing interpolated raw."""
    source = js(client)
    card = source[source.index("function renderHazard"):]
    card = card[:card.index("function renderRail")]
    assert "${h.rule.text}" not in card


def test_an_ability_the_rule_punishes_is_marked_on_its_button(client):
    source = js(client)
    assert "penalisedStats" in source
    buttons = source[source.index("function renderAbilities"):]
    assert "penalised" in buttons and "penalty" in buttons


def test_the_marked_button_has_somewhere_to_show_it(client):
    theme = client.get("/static/css/theme.css").get_data(as_text=True)
    assert ".boss-rule" in theme and ".ability .penalty" in theme
