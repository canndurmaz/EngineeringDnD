from narrator.prompts import SAMPLING, SYSTEM_PROMPT, build


def turn_job():
    return {"kind": "turn", "premise": '"Kestrel" - a trainer aircraft',
            "phase": "Integration", "actor_name": "Priya",
            "actor_class": "Control Systems Engineer",
            "ability_name": "Kalman Filter", "outcome": "success",
            "natural": 11, "total": 14, "dc": 13,
            "hazard": {"name": "Harmonic Coupling", "severity": 18,
                       "max_severity": 30},
            "changes": [{"kind": "hazard_damage", "amount": 7}]}


def test_system_prompt_forbids_inventing_numbers():
    assert "Never invent numbers" in SYSTEM_PROMPT


def test_system_prompt_forbids_deciding_what_happens_next():
    assert "Never decide what happens next" in SYSTEM_PROMPT


def test_build_returns_system_then_user():
    messages = build(turn_job())
    assert [m["role"] for m in messages] == ["system", "user"]


def test_user_message_carries_every_committed_fact():
    body = build(turn_job())[1]["content"]
    for fragment in ("Kestrel", "Integration", "Harmonic Coupling", "Priya",
                     "Control Systems Engineer", "Kalman Filter", "SUCCESS"):
        assert fragment in body


def test_user_message_states_the_outcome_in_capitals():
    assert "SUCCESS" in build(turn_job())[1]["content"]


def test_fumble_outcome_is_labelled_distinctly():
    job = turn_job()
    job["outcome"] = "fumble"
    assert "FUMBLE" in build(job)[1]["content"]


def test_missing_hazard_still_builds():
    job = turn_job()
    job["hazard"] = None
    assert build(job)[1]["content"]


def test_phase_job_builds_an_interlude_prompt():
    body = build({"kind": "phase", "phase": "Qualification",
                  "premise": "A trainer aircraft"})[1]["content"]
    assert "Qualification" in body


def test_genesis_job_asks_for_a_premise():
    body = build({"kind": "genesis", "room_name": "Kestrel",
                  "archetype_hint": "a four-seat trainer aircraft"})[1]["content"]
    assert "Kestrel" in body and "four-seat" in body


def test_hazards_job_asks_for_a_specific_count():
    body = build({"kind": "hazards", "phase": "Design", "count": 3,
                  "premise": "A trainer aircraft",
                  "existing": ["Thermal Budget Overrun"]})[1]["content"]
    assert "3" in body and "Design" in body


def test_sampling_caps_tokens_for_a_slow_cpu():
    assert SAMPLING["max_tokens"] <= 200
    assert SAMPLING["stop"] == ["\n\n"]
