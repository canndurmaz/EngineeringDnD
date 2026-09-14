"""Where in the machine a problem lives.

The archetype's subsystem list is the map's whole vocabulary, and the placement
is campaign data, so both are pinned here: the data file's shape, and the fact
that a seed still reproduces a campaign exactly now that placement rides on the
same Dice.
"""
import pytest
from engine.dice import Dice
from engine.phases import PHASES, build_campaign, load_archetypes, load_hazard_templates


@pytest.fixture(scope="module")
def templates():
    return load_hazard_templates()


@pytest.fixture(scope="module")
def archetypes():
    return load_archetypes()


# --- the data ---------------------------------------------------------------

def test_every_archetype_lists_five_to_seven_subsystems(archetypes):
    for archetype in archetypes:
        count = len(archetype.get("subsystems") or [])
        assert 5 <= count <= 7, f"{archetype['id']} has {count} subsystems"


def test_every_subsystem_has_an_id_a_name_and_a_blurb(archetypes):
    for archetype in archetypes:
        for subsystem in archetype["subsystems"]:
            assert subsystem["id"] and isinstance(subsystem["id"], str)
            assert subsystem["name"] and isinstance(subsystem["name"], str)
            assert subsystem["blurb"] and isinstance(subsystem["blurb"], str)


def test_subsystem_ids_are_unique_within_an_archetype(archetypes):
    for archetype in archetypes:
        ids = [s["id"] for s in archetype["subsystems"]]
        assert len(ids) == len(set(ids)), archetype["id"]


# --- placement --------------------------------------------------------------

def test_every_hazard_lands_in_a_subsystem_of_its_archetype(templates, archetypes):
    for archetype in archetypes:
        known = {s["id"] for s in archetype["subsystems"]}
        hazards = build_campaign(Dice(7), templates, archetype["subsystems"])
        for hazard in hazards:
            assert hazard["subsystem"] in known, (
                f"{archetype['id']}: {hazard['id']} is in "
                f"{hazard['subsystem']!r}, which is not a subsystem of it")


def test_a_phase_spreads_across_more_than_one_subsystem(templates, archetypes):
    """A map with every light on one node is not a map."""
    hazards = build_campaign(Dice(7), templates, archetypes[0]["subsystems"])
    for index in range(len(PHASES)):
        placed = {h["subsystem"] for h in hazards
                  if h["phase_index"] == index}
        assert len(placed) >= 2


def test_placement_is_deterministic_for_a_seed(templates, archetypes):
    subsystems = archetypes[1]["subsystems"]
    first = build_campaign(Dice(11), templates, subsystems)
    second = build_campaign(Dice(11), templates, subsystems)
    assert first == second
    assert [h["subsystem"] for h in first] == [h["subsystem"] for h in second]


def test_a_different_seed_places_them_differently(templates, archetypes):
    subsystems = archetypes[1]["subsystems"]
    a = [h["subsystem"] for h in build_campaign(Dice(1), templates, subsystems)]
    b = [h["subsystem"] for h in build_campaign(Dice(2), templates, subsystems)]
    assert a != b


def test_omitting_the_subsystems_leaves_the_placement_blank(templates):
    """An archetype written before the map existed still builds a campaign."""
    hazards = build_campaign(Dice(3), templates)
    assert all(h["subsystem"] == "" for h in hazards)
