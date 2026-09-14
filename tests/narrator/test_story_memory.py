"""The narrator remembers.

Each turn used to be narrated from a fact block alone, so the prose read as
three restarts rather than one story. These pin the two halves of the fix: what
the prompt carries, and where the service reads it from.
"""
import pytest
from broker import EventBroker
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from narrator.base import FakeNarrator
from narrator.prompts import (HISTORY_CHARS, HISTORY_LIMIT, SYSTEM_PROMPT,
                              build)
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker
from service import GameService


def turn_job(**extra):
    job = {"kind": "turn", "premise": '"Kestrel" - a trainer aircraft',
           "phase": "Integration", "actor_name": "Priya",
           "actor_class": "Control Systems Engineer",
           "ability_name": "Kalman Filter", "outcome": "success",
           "natural": 11, "total": 14, "dc": 13,
           "hazard": {"name": "Harmonic Coupling", "severity": 18,
                      "max_severity": 30},
           "changes": [{"kind": "hazard_damage", "amount": 7}]}
    job.update(extra)
    return job


def body(job):
    return build(job)[1]["content"]


# --- the prompt --------------------------------------------------------------

def test_the_previous_narration_reaches_the_prompt():
    text = body(turn_job(history=["The rig settled and the ringing stopped."]))
    assert "STORY SO FAR" in text
    assert "The rig settled and the ringing stopped." in text


def test_both_remembered_passages_reach_the_prompt():
    text = body(turn_job(history=["First passage here.", "Second passage here."]))
    assert "First passage here." in text and "Second passage here." in text


def test_the_first_turn_of_a_room_omits_the_history_section_cleanly():
    """No heading, no empty bullet: an empty heading is exactly the kind of
    thing a small model fills in itself."""
    text = body(turn_job())
    assert "STORY SO FAR" not in text
    assert "SINCE THEN" not in text
    assert text.startswith("PROJECT:")


def test_an_empty_history_list_is_the_same_as_none():
    assert "STORY SO FAR" not in body(turn_job(history=[]))
    assert "STORY SO FAR" not in body(turn_job(history=["", "   "]))


def test_at_most_two_passages_are_remembered():
    text = body(turn_job(history=["one.", "two.", "three.", "four."]))
    assert "one." not in text and "two." not in text
    assert "three." in text and "four." in text
    assert HISTORY_LIMIT == 2


def test_each_remembered_passage_is_truncated():
    """History must not blow the 1024-token context it shares with the facts."""
    long = "The bracket sang under load. " * 40
    text = body(turn_job(history=[long]))
    remembered = [line for line in text.splitlines() if line.startswith("- ")][0]
    assert len(remembered) < HISTORY_CHARS + 20
    assert remembered.endswith("...")


def test_the_prompt_stays_well_inside_the_context_window():
    """Two full-length passages plus the facts, in characters, against 1024
    tokens of context at roughly four characters a token."""
    text = body(turn_job(history=["x" * 600, "y" * 600],
                         severity_remaining=0.5, same_engineer=True))
    assert len(text) + len(SYSTEM_PROMPT) < 1024 * 4 * 0.5


def test_the_prompt_says_what_has_changed_since():
    text = body(turn_job(history=["Something happened."],
                         severity_remaining=0.5, same_engineer=True))
    assert "SINCE THEN" in text
    assert "Integration" in text
    assert "half" in text
    assert "the same engineer is acting again" in text


def test_a_new_engineer_is_named_as_a_change():
    text = body(turn_job(history=["Something happened."],
                         severity_remaining=0.9, same_engineer=False))
    assert "a different engineer has the floor now" in text


def test_the_remaining_severity_is_spelled_out_not_digits():
    """narrator.filters drops any sentence quoting a number the engine did not
    commit, so a model echoing "60%" would lose the whole passage."""
    text = body(turn_job(history=["Something happened."],
                         severity_remaining=0.62, same_engineer=False))
    since = [line for line in text.splitlines() if line.startswith("SINCE THEN")][0]
    assert not any(ch.isdigit() for ch in since)


def test_the_facts_still_follow_the_history():
    text = body(turn_job(history=["Something happened."], severity_remaining=0.5))
    for fragment in ("Kestrel", "Harmonic Coupling", "Priya", "Kalman Filter",
                     "SUCCESS"):
        assert fragment in text
    assert text.index("STORY SO FAR") < text.index("PROJECT:")


# --- the system prompt -------------------------------------------------------

def test_the_system_prompt_asks_for_continuation():
    assert "CONTINUITY" in SYSTEM_PROMPT
    assert "continuous story" in SYSTEM_PROMPT


def test_the_system_prompt_forbids_restating_the_previous_passage():
    assert "do not " in SYSTEM_PROMPT.lower()
    assert "restate" in SYSTEM_PROMPT


def test_the_system_prompt_forbids_opening_on_the_engineers_name():
    assert "Never open a passage with the engineer's name" in SYSTEM_PROMPT
    assert "vary" in SYSTEM_PROMPT.lower()


@pytest.mark.parametrize("rule", [
    "Never invent numbers",
    "Never contradict the stated outcome",
    "Never decide what happens next",
    "Do not mention dice, rolls, or difficulty classes",
    "2-3 sentences",
])
def test_every_existing_prompt_rule_survives(rule):
    assert rule in SYSTEM_PROMPT


# --- where the memory comes from ---------------------------------------------

@pytest.fixture
def rig(tmp_path):
    queue = NarrationQueue()
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes(), queue=queue, broker=EventBroker())
    room_id = svc.create_room("Kestrel", "aircraft")
    ada = svc.join_room(room_id, "Ada", "computer_scientist")
    ben = svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    return svc, queue, room_id, ada["player_id"], ben["player_id"]


def turn_jobs(queue):
    """Drain the queue, keeping only the turn jobs."""
    out = []
    while True:
        job = queue.get(timeout=0.02)
        if job is None:
            return out
        if job.get("kind") == "turn":
            out.append(job)


def narrate(queue, svc, text):
    NarrationWorker(queue, FakeNarrator(text), svc, EventBroker()).run_once(
        timeout=0.05)


def test_the_first_turn_of_a_room_carries_no_history(rig):
    svc, queue, room_id, ada, _ = rig
    svc.act(room_id, ada, "unit_test_barrage")
    assert turn_jobs(queue)[0]["history"] == []


def test_the_second_turn_carries_the_first_narration(rig):
    svc, queue, room_id, ada, ben = rig
    svc.act(room_id, ada, "unit_test_barrage")
    narrate(queue, svc, "The suite went green at last.")
    svc.act(room_id, ben, "shop_floor_fix")
    assert turn_jobs(queue)[0]["history"] == ["The suite went green at last."]


def test_only_the_last_two_narrations_are_remembered(rig):
    svc, queue, room_id, ada, ben = rig
    for actor, ability, line in [(ada, "unit_test_barrage", "First line."),
                                 (ben, "shop_floor_fix", "Second line."),
                                 (ada, "unit_test_barrage", "Third line.")]:
        svc.act(room_id, actor, ability)
        narrate(queue, svc, line)
    svc.act(room_id, ben, "shop_floor_fix")
    assert turn_jobs(queue)[0]["history"] == ["Second line.", "Third line."]


def test_the_job_carries_the_remaining_severity_as_a_fraction(rig):
    svc, queue, room_id, ada, _ = rig
    svc.act(room_id, ada, "unit_test_barrage")
    remaining = turn_jobs(queue)[0]["severity_remaining"]
    assert isinstance(remaining, float) and 0.0 <= remaining <= 1.0


def test_a_different_engineer_is_reported_as_a_change(rig):
    svc, queue, room_id, ada, ben = rig
    svc.act(room_id, ada, "unit_test_barrage")
    narrate(queue, svc, "A line.")
    svc.act(room_id, ben, "shop_floor_fix")
    # narrate() already consumed the first turn's job; this is the second.
    assert turn_jobs(queue)[0]["same_engineer"] is False


def test_the_same_engineer_twice_running_is_reported_as_such(tmp_path):
    """A solo table: the previous turn is always the same pair of hands."""
    queue = NarrationQueue()
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes(), queue=queue, broker=EventBroker())
    room_id = svc.create_room("Kestrel", "aircraft")
    ada = svc.join_room(room_id, "Ada", "computer_scientist")["player_id"]
    svc.start_game(room_id)
    svc.act(room_id, ada, "unit_test_barrage")
    narrate(queue, svc, "A line.")                # consumes the first turn's job
    svc.act(room_id, ada, "unit_test_barrage")
    assert turn_jobs(queue)[0]["same_engineer"] is True


def test_an_interlude_is_not_remembered_as_a_turn(rig):
    """A phase interlude is a narration event too, but it belongs to no turn and
    must not be mistaken for the previous passage of this scene."""
    svc, queue, room_id, ada, _ = rig
    room = svc._room(room_id)
    room.append_event("narration", None,
                      {"event_seq": None, "text": "A gate closed.",
                       "source": "x", "job_kind": "phase"})
    svc.act(room_id, ada, "unit_test_barrage")
    assert turn_jobs(queue)[0]["history"] == []


# --- the template narrator is untouched --------------------------------------

def test_the_template_narrator_still_works_with_history_in_the_job():
    from narrator.fallback import TemplateNarrator
    text = TemplateNarrator().narrate(turn_job(
        event_seq=3, history=["Something earlier."], severity_remaining=0.4,
        same_engineer=True))
    assert text and "Priya" in text
