"""The story log says what happened, not that something happened.

Reported from a live game: five identical "The plan is revised." lines before
the programme had even started, and every other line a static string that threw
away the payload it was handed.
"""


def game_js(client):
    return client.get("/static/js/game.js").get_data(as_text=True)


def test_campaign_updated_produces_no_log_entry(client):
    """Genesis emits one per phase. It is bookkeeping: the player already sees
    the result, because the hazard names themselves change."""
    body = game_js(client)
    assert "The plan is revised" not in body
    # Still subscribed -- the event refreshes the panels; it just says nothing.
    assert '"campaign_updated"' in body
    assert "campaign_updated:" not in body


def test_player_joined_names_the_player_and_their_discipline(client):
    body = game_js(client)
    assert "player_joined: (e) =>" in body
    assert "${esc(e.name)} joins as ${esc(className(e.class_id))}." in body


def test_a_class_id_is_made_readable(client):
    body = game_js(client)
    assert "CLASS_NAMES[id] ||" in body
    assert 'replace(/_/g, " ")' in body


def test_passed_names_the_engineer_who_passed(client):
    body = game_js(client)
    assert "${esc(who)} passes." in body
    assert '"Turn passed."' in body          # the fallback when it cannot resolve


def test_hazard_defeated_names_the_hazard(client):
    body = game_js(client)
    assert "${esc(e.name)} closed." in body
    assert '"Problem closed."' in body


def test_phase_advanced_names_the_phase(client):
    assert "gate cleared." in game_js(client)


def test_game_over_reads_differently_per_outcome(client):
    body = game_js(client)
    for result in ("win", "lose_budget", "lose_schedule", "lose_burnout"):
        assert f"{result}:" in body
    lines = body[body.index("const OVER = {"):]
    lines = lines[:lines.index("};")]
    texts = [line.split(":", 1)[1] for line in lines.splitlines() if ":" in line][1:]
    assert len(set(texts)) == len(texts), "two outcomes read identically"


def test_every_payload_value_in_a_log_line_is_escaped(client):
    """These payloads carry model-written hazard names and player-typed display
    names; this codebase has already had one stored XSS."""
    body = game_js(client)
    labels = body[body.index("const LABELS = {"):body.index("const logLine")]
    for token in ("e.name", "e.phase", "e.class_id", "who"):
        for line in labels.splitlines():
            if "${" + token in line:
                assert "esc(" in line, line
