"""The character-select screen has to teach the stats and tell the truth
about whether the programme can start."""
from tests.api.conftest import make_room

STAT_BLURBS = [
    ("RIGOR", "Analysis, math, verification"),
    ("INTUITION", "Debugging instinct, pattern recognition"),
    ("CRAFT", "Fabrication, soldering, rework, bench work"),
    ("SYSTEMS", "Architecture, interfaces, whole-system reasoning"),
    ("COMMS", "Stakeholders, documentation, persuasion"),
    ("GRIT", "Endurance, stress tolerance"),
]


def _join_page(client):
    room_id = make_room(client)
    return client.get(f"/room/{room_id}/join").get_data(as_text=True)


def test_the_join_page_explains_every_stat(client):
    body = _join_page(client)
    for stat, blurb in STAT_BLURBS:
        assert stat in body, stat
        assert blurb in body, blurb


def test_the_explainer_is_collapsible_and_open_by_default(client):
    body = _join_page(client)
    assert '<details class="stat-guide" open>' in body


def test_the_explainer_states_how_the_numbers_are_used(client):
    body = _join_page(client)
    assert "d20 + stat modifier" in body
    assert "8 + GRIT mod + 2 &times; level" in body
    assert "4 + the better of RIGOR and SYSTEMS mods" in body


def test_the_start_button_ships_disabled(client):
    """Nobody is seated when the page is first served, so the button says so."""
    body = _join_page(client)
    start = body[body.index('id="start"'):]
    assert "disabled" in start[:start.index("</button>")]
    assert "Waiting for engineers" in body


def test_lobby_js_gates_start_on_the_seated_count(client):
    js = client.get("/static/js/lobby.js").get_data(as_text=True)
    assert "Start the programme (${seated} seated)" in js
    assert "startButton.disabled = seated === 0" in js


def test_lobby_js_reveals_the_roll_and_offers_no_reroll(client):
    js = client.get("/static/js/lobby.js").get_data(as_text=True)
    assert "revealTheRoll" in js
    assert "Take your seat" in js
    # The reveal must not redirect on its own -- the player clicks through it.
    assert "revealing = true" in js
    assert "if (revealing) return state;" in js


def test_the_explainer_mentions_the_clamp(client):
    body = _join_page(client)
    assert "8&ndash;16" in body
    assert "pulled back toward the middle" in body


def test_lobby_js_shows_the_clamp_adjustment(client):
    js = client.get("/static/js/lobby.js").get_data(as_text=True)
    assert 'title="clamped to the 8-16 range"' in js
    assert "roll.value !== roll.total" in js
