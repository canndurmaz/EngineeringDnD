import pytest
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from engine.rules import RuleError
from service import GameService, ServiceError


@pytest.fixture
def svc(tmp_path):
    return GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                       load_archetypes())


def seat_two(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    a = svc.join_room(room_id, "Ada", "computer_scientist")
    b = svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    return room_id, a, b


def test_create_room_returns_a_short_code(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    assert 4 <= len(room_id) <= 8 and room_id.isalnum()


def test_created_room_appears_in_the_lobby(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    assert room_id in {r["room_id"] for r in svc.list_rooms()}


def test_room_ids_are_unique(svc):
    ids = {svc.create_room(f"R{i}", "car") for i in range(25)}
    assert len(ids) == 25


def test_unknown_archetype_is_rejected(svc):
    with pytest.raises(ServiceError, match="archetype"):
        svc.create_room("Kestrel", "time_machine")


def test_campaign_is_playable_immediately_after_creation(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    assert len(svc.snapshot(room_id)["hazards"]) >= 15


def test_join_returns_a_character_and_a_token(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    joined = svc.join_room(room_id, "Ada", "computer_scientist")
    assert joined["character"]["class_id"] == "computer_scientist"
    assert joined["token"] and joined["player_id"]


def test_joining_a_taken_class_is_rejected(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    svc.join_room(room_id, "Ada", "computer_scientist")
    with pytest.raises(ServiceError, match="taken"):
        svc.join_room(room_id, "Ben", "computer_scientist")


def test_joining_an_unknown_class_is_rejected(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    with pytest.raises(ServiceError, match="class"):
        svc.join_room(room_id, "Ada", "wizard")


def test_joining_an_unknown_room_is_rejected(svc):
    with pytest.raises(ServiceError, match="room"):
        svc.join_room("nope", "Ada", "computer_scientist")


def test_player_by_token_round_trips(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    joined = svc.join_room(room_id, "Ada", "computer_scientist")
    found = svc.player_by_token(room_id, joined["token"])
    assert found["player_id"] == joined["player_id"]


def test_start_game_activates_and_seats_a_hazard(svc):
    room_id, _, _ = seat_two(svc)
    snap = svc.snapshot(room_id)
    assert snap["room"]["status"] == "active"
    assert snap["active_hazard_id"] is not None


def test_start_game_with_no_players_is_rejected(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    with pytest.raises(ServiceError, match="player"):
        svc.start_game(room_id)


def test_turn_order_follows_join_order(svc):
    room_id, a, b = seat_two(svc)
    assert svc.snapshot(room_id)["turn"]["order"] == [a["player_id"], b["player_id"]]


def test_act_returns_a_roll_result_and_persists_it(svc):
    room_id, a, _ = seat_two(svc)
    result = svc.act(room_id, a["player_id"], "unit_test_barrage")
    assert result["outcome"] in ("crit", "success", "failure", "fumble")
    assert result["event_seq"] > 0


def test_act_advances_the_turn(svc):
    room_id, a, b = seat_two(svc)
    svc.act(room_id, a["player_id"], "unit_test_barrage")
    snap = svc.snapshot(room_id)
    assert snap["turn"]["order"][snap["turn"]["turn_index"]] == b["player_id"]


def test_acting_out_of_turn_raises_a_rule_error(svc):
    room_id, _, b = seat_two(svc)
    with pytest.raises(RuleError, match="turn"):
        svc.act(room_id, b["player_id"], "shop_floor_fix")


def test_act_appends_events_to_the_log(svc):
    room_id, a, _ = seat_two(svc)
    before = len(svc.events_since(room_id, 0))
    svc.act(room_id, a["player_id"], "unit_test_barrage")
    assert len(svc.events_since(room_id, 0)) > before


def test_act_creates_a_pending_narration(svc):
    room_id, a, _ = seat_two(svc)
    result = svc.act(room_id, a["player_id"], "unit_test_barrage")
    assert svc.narration(room_id, result["event_seq"])["status"] == "pending"


def test_state_is_committed_before_narration_is_queued(svc):
    seen = []

    class RecordingQueue:
        def submit(self, job):
            seen.append(svc.snapshot(job["room_id"])["characters"])

    room_id = svc.create_room("Kestrel", "aircraft")
    a = svc.join_room(room_id, "Ada", "computer_scientist")
    svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    svc.queue = RecordingQueue()
    svc.act(room_id, a["player_id"], "unit_test_barrage")
    assert seen and seen[0][a["player_id"]]["focus"] < seen[0][a["player_id"]]["max_focus"]


def test_end_turn_passes_without_acting(svc):
    room_id, a, b = seat_two(svc)
    svc.end_turn(room_id, a["player_id"])
    snap = svc.snapshot(room_id)
    assert snap["turn"]["order"][snap["turn"]["turn_index"]] == b["player_id"]


def test_full_round_triggers_the_hazard_attack(svc):
    room_id, a, b = seat_two(svc)
    svc.end_turn(room_id, a["player_id"])
    result = svc.end_turn(room_id, b["player_id"])
    assert any(e["kind"] == "hazard_attack" for e in result["events"])


def test_defeating_a_hazard_seats_the_next_one(svc):
    room_id, a, _ = seat_two(svc)
    state = svc.snapshot(room_id)
    first = state["active_hazard_id"]
    svc._force_defeat_active_hazard(room_id)     # test seam, documented below
    assert svc.snapshot(room_id)["active_hazard_id"] != first


def test_clearing_a_phase_levels_the_party(svc):
    room_id, a, b = seat_two(svc)
    svc._force_clear_phase(room_id)
    snap = svc.snapshot(room_id)
    assert snap["room"]["phase_index"] == 1
    assert all(c["level"] == 2 for c in snap["characters"].values())


def test_level_choice_is_honoured_when_set(svc):
    room_id, a, b = seat_two(svc)
    svc.set_level_choice(room_id, a["player_id"], "COMMS")
    before = svc.snapshot(room_id)["characters"][a["player_id"]]["stats"]["COMMS"]
    svc._force_clear_phase(room_id)
    after = svc.snapshot(room_id)["characters"][a["player_id"]]["stats"]["COMMS"]
    assert after == before + 1


def test_level_choice_defaults_to_the_class_primary_stat(svc):
    room_id, a, b = seat_two(svc)
    before = svc.snapshot(room_id)["characters"][a["player_id"]]["stats"]["RIGOR"]
    svc._force_clear_phase(room_id)
    after = svc.snapshot(room_id)["characters"][a["player_id"]]["stats"]["RIGOR"]
    assert after == before + 1


def test_invalid_level_choice_is_rejected(svc):
    room_id, a, _ = seat_two(svc)
    with pytest.raises(ServiceError, match="stat"):
        svc.set_level_choice(room_id, a["player_id"], "LUCK")


def test_losing_the_budget_ends_the_game(svc):
    room_id, a, b = seat_two(svc)
    svc._set_party(room_id, budget=0)
    svc.end_turn(room_id, a["player_id"])
    assert svc.snapshot(room_id)["room"]["status"] == "lost_budget"


def test_a_finished_room_rejects_further_actions(svc):
    room_id, a, b = seat_two(svc)
    svc._set_party(room_id, budget=0)
    svc.end_turn(room_id, a["player_id"])
    with pytest.raises(ServiceError, match="over"):
        svc.act(room_id, b["player_id"], "shop_floor_fix")


def test_lobby_index_tracks_the_phase(svc):
    room_id, a, b = seat_two(svc)
    svc._force_clear_phase(room_id)
    row = [r for r in svc.list_rooms() if r["room_id"] == room_id][0]
    assert row["phase_index"] == 1


def test_acting_before_the_game_starts_is_rejected(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    joined = svc.join_room(room_id, "Ada", "computer_scientist")
    with pytest.raises(ServiceError, match="not started"):
        svc.act(room_id, joined["player_id"], "unit_test_barrage")


def test_a_room_survives_a_fresh_service_instance(tmp_path):
    catalog, templates, archetypes = (
        load_catalog(), load_hazard_templates(), load_archetypes())
    first = GameService(str(tmp_path), catalog, templates, archetypes)
    room_id, a, _ = seat_two(first)
    first.act(room_id, a["player_id"], "unit_test_barrage")
    focus = first.snapshot(room_id)["characters"][a["player_id"]]["focus"]

    second = GameService(str(tmp_path), catalog, templates, archetypes)
    assert second.snapshot(room_id)["characters"][a["player_id"]]["focus"] == focus


def _action_event(svc, room_id, result):
    events = svc._room(room_id).events_since(result["event_seq"] - 1)
    return next(e for e in events if e["kind"] == "action")


def test_action_event_hides_an_unrevealed_hazard_dc(svc):
    room_id, a, _ = seat_two(svc)
    result = svc.act(room_id, a["player_id"], "unit_test_barrage")
    assert _action_event(svc, room_id, result)["payload"]["dc"] is None


def test_action_event_carries_the_dc_once_it_is_revealed(svc):
    room_id, a, _ = seat_two(svc)
    state = svc.snapshot(room_id)
    hazard = next(h for h in state["hazards"]
                  if h["id"] == state["active_hazard_id"])
    hazard.setdefault("revealed", []).append("dc")
    svc._room(room_id).save_state(state)

    result = svc.act(room_id, a["player_id"], "unit_test_barrage")
    payload = _action_event(svc, room_id, result)["payload"]
    assert payload["dc"] == result["dc"] and isinstance(payload["dc"], int)
