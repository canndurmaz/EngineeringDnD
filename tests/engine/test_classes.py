import json
import pytest
from engine.classes import STATS, Catalog, CatalogError, load_catalog

CLASS_IDS = [
    "mechanical_engineer", "computer_scientist", "ee_engineer", "system_engineer",
    "product_manager", "mechanical_technician", "electrical_technician",
    "control_systems_engineer", "mechatronics_engineer",
]


def test_stats_are_the_canonical_six():
    assert STATS == ("RIGOR", "INTUITION", "CRAFT", "SYSTEMS", "COMMS", "GRIT")


def test_all_nine_classes_load():
    cat = load_catalog()
    assert sorted(cat.classes) == sorted(CLASS_IDS)


def test_class_stats_are_valid_and_distinct():
    cat = load_catalog()
    for cls in cat.classes.values():
        assert cls.primary in STATS and cls.secondary in STATS
        assert cls.primary != cls.secondary


def test_abilities_resolve_to_real_classes():
    cat = load_catalog()
    for ab in cat.abilities.values():
        assert ab.class_id in cat.classes


def test_unlocked_for_phase_zero_returns_starting_abilities():
    cat = load_catalog()
    starters = cat.unlocked_for("product_manager", 0)
    assert all(a.unlock_phase == 0 for a in starters)


def test_unlocked_is_cumulative_across_phases():
    cat = load_catalog()
    early = cat.unlocked_for("product_manager", 0)
    late = cat.unlocked_for("product_manager", 4)
    assert set(a.id for a in early) <= set(a.id for a in late)


def test_rejects_ability_with_unknown_stat(tmp_path):
    (tmp_path / "classes.json").write_text(json.dumps({
        "x": {"name": "X", "primary": "RIGOR", "secondary": "GRIT",
              "role": "r", "blurb": "b"}}))
    (tmp_path / "abilities.json").write_text(json.dumps([{
        "id": "a", "name": "A", "class": "x", "focus_cost": 0, "stat": "MAGIC",
        "dc_mod": 0, "unlock_phase": 0, "target": "hazard",
        "on_success": [], "on_fail": [], "flavor": "f"}]))
    with pytest.raises(CatalogError, match="MAGIC"):
        load_catalog(str(tmp_path))


def test_rejects_ability_pointing_at_missing_class(tmp_path):
    (tmp_path / "classes.json").write_text(json.dumps({}))
    (tmp_path / "abilities.json").write_text(json.dumps([{
        "id": "a", "name": "A", "class": "ghost", "focus_cost": 0, "stat": "RIGOR",
        "dc_mod": 0, "unlock_phase": 0, "target": "hazard",
        "on_success": [], "on_fail": [], "flavor": "f"}]))
    with pytest.raises(CatalogError, match="ghost"):
        load_catalog(str(tmp_path))


def test_rejects_duplicate_ability_ids(tmp_path):
    (tmp_path / "classes.json").write_text(json.dumps({
        "x": {"name": "X", "primary": "RIGOR", "secondary": "GRIT",
              "role": "r", "blurb": "b"}}))
    one = {"id": "a", "name": "A", "class": "x", "focus_cost": 0, "stat": "RIGOR",
           "dc_mod": 0, "unlock_phase": 0, "target": "hazard",
           "on_success": [], "on_fail": [], "flavor": "f"}
    (tmp_path / "abilities.json").write_text(json.dumps([one, one]))
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog(str(tmp_path))
