import pytest
from narrator.fallback import TemplateNarrator


def turn_job(outcome="success", **extra):
    job = {"kind": "turn", "event_seq": 7, "premise": "A trainer aircraft",
           "phase": "Integration", "actor_name": "Priya",
           "actor_class": "Control Systems Engineer",
           "ability_name": "Kalman Filter", "outcome": outcome,
           "natural": 11, "total": 14, "dc": 13,
           "hazard": {"name": "Harmonic Coupling", "severity": 18,
                      "max_severity": 30},
           "changes": [{"kind": "hazard_damage", "amount": 7}]}
    job.update(extra)
    return job


@pytest.fixture
def narrator():
    return TemplateNarrator()


def test_name_identifies_the_source(narrator):
    assert narrator.name == "template"


def test_success_narration_is_nonempty(narrator):
    assert narrator.narrate(turn_job("success")).strip()


@pytest.mark.parametrize("outcome", ["crit", "success", "failure", "fumble"])
def test_every_outcome_produces_text(narrator, outcome):
    assert narrator.narrate(turn_job(outcome)).strip()


def test_narration_names_the_actor_and_ability(narrator):
    text = narrator.narrate(turn_job())
    assert "Priya" in text and "Kalman Filter" in text


def test_narration_mentions_the_hazard(narrator):
    assert "Harmonic Coupling" in narrator.narrate(turn_job())


def test_narration_varies_with_the_event_sequence(narrator):
    a = narrator.narrate(turn_job(event_seq=1))
    b = narrator.narrate(turn_job(event_seq=2))
    c = narrator.narrate(turn_job(event_seq=3))
    assert len({a, b, c}) > 1


def test_narration_is_stable_for_the_same_event(narrator):
    assert narrator.narrate(turn_job()) == narrator.narrate(turn_job())


def test_phase_job_produces_an_interlude(narrator):
    text = narrator.narrate({"kind": "phase", "event_seq": 3,
                             "premise": "A trainer aircraft",
                             "phase": "Qualification"})
    assert "Qualification" in text


def test_genesis_job_produces_a_premise(narrator):
    text = narrator.narrate({"kind": "genesis", "event_seq": 1,
                             "archetype_hint": "a four-seat trainer aircraft",
                             "room_name": "Kestrel"})
    assert "Kestrel" in text


def test_narration_never_claims_a_die_roll(narrator):
    for outcome in ("crit", "success", "failure", "fumble"):
        text = narrator.narrate(turn_job(outcome)).lower()
        assert "d20" not in text and "rolled" not in text


def test_missing_hazard_does_not_crash(narrator):
    assert narrator.narrate(turn_job(hazard=None)).strip()
