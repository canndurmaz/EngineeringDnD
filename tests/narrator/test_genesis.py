import pytest
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from narrator.genesis import genesis_jobs, parse_hazard_lines
from narrator.queue_ import NarrationQueue
from service import GameService


@pytest.fixture
def svc(tmp_path):
    return GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                       load_archetypes(), queue=NarrationQueue())


def test_parses_dash_separated_lines():
    text = "Thermal Soak - the avionics bay never cools below 60 C.\n" \
           "Wire Chafe - the loom rubs on a bracket nobody inspected."
    parsed = parse_hazard_lines(text, 2)
    assert parsed[0] == ("Thermal Soak",
                         "the avionics bay never cools below 60 C.")
    assert len(parsed) == 2


def test_parses_em_dash_and_colon_separators():
    text = "Thermal Soak — it runs hot.\nWire Chafe: the loom rubs."
    assert len(parse_hazard_lines(text, 2)) == 2


def test_strips_leading_bullets_and_numbers():
    text = "1. Thermal Soak - it runs hot.\n- Wire Chafe - the loom rubs."
    names = [n for n, _ in parse_hazard_lines(text, 2)]
    assert names == ["Thermal Soak", "Wire Chafe"]


def test_returns_at_most_the_expected_count():
    text = "\n".join(f"Problem {i} - description {i}." for i in range(9))
    assert len(parse_hazard_lines(text, 3)) == 3


def test_skips_lines_without_a_separator():
    text = "Here are the problems\nThermal Soak - it runs hot."
    assert parse_hazard_lines(text, 2) == [("Thermal Soak", "it runs hot.")]


def test_rejects_absurdly_long_names():
    text = ("X" * 120) + " - a description.\nThermal Soak - it runs hot."
    assert [n for n, _ in parse_hazard_lines(text, 2)] == ["Thermal Soak"]


def test_empty_text_parses_to_nothing():
    assert parse_hazard_lines("", 3) == []


def test_genesis_jobs_cover_the_premise_and_all_five_phases(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    jobs = genesis_jobs(room_id, svc.snapshot(room_id),
                        {"id": "aircraft", "hint": "a trainer aircraft"})
    assert sum(1 for j in jobs if j["kind"] == "genesis") == 1
    assert sum(1 for j in jobs if j["kind"] == "hazards") == 5


def test_genesis_jobs_are_low_priority(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    jobs = genesis_jobs(room_id, svc.snapshot(room_id),
                        {"id": "aircraft", "hint": "a trainer aircraft"})
    assert all(j["priority"] >= 2 for j in jobs)


def test_create_room_enqueues_genesis(svc):
    svc.create_room("Kestrel", "aircraft")
    assert svc.queue.pending() == 6


def test_set_premise_persists(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    svc.set_premise(room_id, "A trainer aircraft for a stubborn customer.")
    assert svc.snapshot(room_id)["room"]["premise"].startswith("A trainer aircraft")


def test_rename_hazards_rewrites_names_in_place(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    svc.rename_hazards(room_id, 0, [("Thermal Soak", "It runs hot.")])
    first = [h for h in svc.snapshot(room_id)["hazards"]
             if h["phase_index"] == 0][0]
    assert first["name"] == "Thermal Soak"
    assert first["description"] == "It runs hot."


def test_rename_hazards_never_alters_mechanics(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    before = [h for h in svc.snapshot(room_id)["hazards"]
              if h["phase_index"] == 0][0]
    svc.rename_hazards(room_id, 0, [("Thermal Soak", "It runs hot.")])
    after = [h for h in svc.snapshot(room_id)["hazards"]
             if h["phase_index"] == 0][0]
    for key in ("severity", "max_severity", "dc", "attack_type", "weakness",
                "is_boss"):
        assert after[key] == before[key]


def test_rename_hazards_skips_defeated_ones(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    svc._force_defeat_active_hazard(room_id)
    defeated = [h for h in svc.snapshot(room_id)["hazards"] if h["defeated"]][0]
    original = defeated["name"]
    svc.rename_hazards(room_id, 0, [("Thermal Soak", "It runs hot.")])
    still = [h for h in svc.snapshot(room_id)["hazards"]
             if h["id"] == defeated["id"]][0]
    assert still["name"] == original


def test_rename_hazards_tolerates_too_few_entries(svc):
    room_id = svc.create_room("Kestrel", "aircraft")
    svc.rename_hazards(room_id, 0, [])           # must not raise
    assert svc.snapshot(room_id)["hazards"]
