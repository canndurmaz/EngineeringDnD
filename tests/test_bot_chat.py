"""Bots say why.

The line a bot posts has to come from the branch its policy actually took -- a
bot that heals somebody and then says it is scoping the problem is worse than a
bot that says nothing.
"""
import pytest
from bots import BOT_LINES, BotRunner, bot_line, decide, turn_number
from broker import EventBroker
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from service import GameService
from tests.test_bots import board


@pytest.fixture(scope="module")
def catalog():
    return load_catalog("data")


@pytest.fixture
def svc(tmp_path):
    return GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                       load_archetypes(), broker=EventBroker())


# --- the lines themselves ---------------------------------------------------

def test_every_branch_has_two_or_three_lines():
    for branch, lines in BOT_LINES.items():
        assert 2 <= len(lines) <= 3, branch
        assert all(line.strip() and len(line) <= 80 for line in lines)


def test_every_branch_the_policy_can_return_has_lines():
    assert {"heal", "debt", "reveal", "buff", "attack", "pass"} <= set(BOT_LINES)


def test_consecutive_turns_never_repeat_a_line():
    """The bug _pick exists to prevent: Random(n).choice() over a short list
    returns the same element for runs of consecutive n."""
    for branch, lines in BOT_LINES.items():
        said = [bot_line(branch, n) for n in range(len(lines) * 3)]
        assert all(a != b for a, b in zip(said, said[1:]))


def test_the_rotation_is_deterministic():
    assert bot_line("heal", 7) == bot_line("heal", 7)


def test_an_unknown_branch_falls_back_rather_than_raising():
    assert bot_line("nonsense", 0) in BOT_LINES["pass"]


def test_the_turn_number_climbs_across_rounds():
    state = {"turn": {"round": 1, "turn_index": 0, "order": ["a", "b"]}}
    assert turn_number(state) == 0
    state["turn"]["turn_index"] = 1
    assert turn_number(state) == 1
    state["turn"].update({"round": 2, "turn_index": 0})
    assert turn_number(state) == 2


# --- the branch the policy took --------------------------------------------

@pytest.mark.parametrize("branch,state_args", [
    ("heal", {"class_id": "mechatronics_engineer",
              "unlocked": ["sensor_fusion", "actuator_integration"],
              "ally_stamina": 4}),
    ("reveal", {"class_id": "ee_engineer",
                "unlocked": ["signal_integrity_scan"], "revealed": ()}),
])
def test_the_branch_name_matches_the_move(catalog, branch, state_args):
    class_id = state_args.pop("class_id")
    unlocked = state_args.pop("unlocked")
    state = board(class_id, unlocked, **state_args)
    assert decide(state, "p1", catalog)[2] == branch


def test_an_empty_hand_is_the_pass_branch(catalog):
    state = board("mechanical_engineer", ["fea_sweep"], focus=0)
    assert decide(state, "p1", catalog) == (None, None, "pass")


# --- a bot at a real table --------------------------------------------------

def _room_with_two_bots(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    svc.add_bot(room_id, "mechanical_engineer")
    svc.add_bot(room_id, "ee_engineer")
    svc.start_game(room_id)
    return room_id


def test_a_bot_speaks_when_it_takes_its_turn(svc):
    room_id = _room_with_two_bots(svc)
    runner = BotRunner(svc, svc.catalog, delay=0.0)
    assert runner.run_once() == 1
    messages = svc.chat_since(room_id, 0)
    assert len(messages) == 1
    assert messages[0]["is_bot"] is True
    assert messages[0]["name"].startswith("Unit-")


def test_the_line_it_posts_is_the_line_for_the_branch_it_took(svc):
    room_id = _room_with_two_bots(svc)
    state = svc.snapshot(room_id)
    player_id = state["turn"]["order"][state["turn"]["turn_index"]]
    expected_branch = decide(state, player_id, svc.catalog)[2]
    expected = bot_line(expected_branch, turn_number(state))

    BotRunner(svc, svc.catalog, delay=0.0).run_once()
    [message] = svc.chat_since(room_id, 0)
    assert message["body"] == expected
    assert message["body"] in BOT_LINES[expected_branch]
    # and it is genuinely the branch-specific line, not a shared fallback
    assert expected_branch != "pass"


def test_a_bot_that_passes_says_so(svc):
    room_id = _room_with_two_bots(svc)
    state = svc.snapshot(room_id)
    player_id = state["turn"]["order"][state["turn"]["turn_index"]]
    # No focus, so nothing is affordable and the policy has to pass.
    with svc._lock(room_id):
        room = svc._room(room_id)
        frozen = room.load_state()
        frozen["characters"][player_id]["focus"] = 0
        room.save_state(frozen)
    BotRunner(svc, svc.catalog, delay=0.0).run_once()
    [message] = svc.chat_since(room_id, 0)
    assert message["body"] in BOT_LINES["pass"]


def test_the_line_is_on_the_event_log_too(svc):
    room_id = _room_with_two_bots(svc)
    BotRunner(svc, svc.catalog, delay=0.0).run_once()
    chats = [e for e in svc.events_since(room_id, 0) if e["kind"] == "chat"]
    assert len(chats) == 1
    assert chats[0]["payload"]["is_bot"] is True


def test_a_bot_that_cannot_speak_still_takes_its_turn(svc, monkeypatch):
    """Commentary must never be able to wedge the game."""
    room_id = _room_with_two_bots(svc)
    def boom(*args, **kwargs):
        raise RuntimeError("chat is down")
    monkeypatch.setattr(svc, "post_chat", boom)
    before = svc.snapshot(room_id)["turn"]
    BotRunner(svc, svc.catalog, delay=0.0).run_once()
    after = svc.snapshot(room_id)["turn"]
    assert (after["round"], after["turn_index"]) != (before["round"],
                                                     before["turn_index"])


def test_two_bots_in_a_row_do_not_say_the_same_thing(svc):
    room_id = _room_with_two_bots(svc)
    runner = BotRunner(svc, svc.catalog, delay=0.0)
    runner.run_once()
    runner.run_once()
    bodies = [m["body"] for m in svc.chat_since(room_id, 0)]
    assert len(bodies) == 2
    if bodies[0] in BOT_LINES.get("attack", []) and bodies[1] in BOT_LINES["attack"]:
        assert bodies[0] != bodies[1]
