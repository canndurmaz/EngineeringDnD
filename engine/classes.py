"""Class and ability catalog, loaded and validated from JSON."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

STATS: tuple[str, ...] = ("RIGOR", "INTUITION", "CRAFT", "SYSTEMS", "COMMS", "GRIT")
TARGETS = {"hazard", "ally", "self", "party"}
ONCE_PER = {None, "hazard", "phase"}


class CatalogError(Exception):
    """Raised when the JSON data files are internally inconsistent."""


@dataclass(frozen=True)
class CharacterClass:
    id: str
    name: str
    primary: str
    secondary: str
    role: str
    blurb: str


@dataclass(frozen=True)
class Ability:
    id: str
    name: str
    class_id: str
    focus_cost: int
    stat: str
    dc_mod: int
    unlock_phase: int
    target: str
    on_success: tuple
    on_fail: tuple
    flavor: str
    fixed_roll: "int | None" = None
    no_crit: bool = False
    no_fumble: bool = False
    stat_alt: "str | None" = None
    once_per: "str | None" = None
    extra_cost: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Catalog:
    classes: dict
    abilities: dict

    def abilities_for(self, class_id: str) -> list:
        return [a for a in self.abilities.values() if a.class_id == class_id]

    def unlocked_for(self, class_id: str, phase_index: int) -> list:
        return [a for a in self.abilities_for(class_id) if a.unlock_phase <= phase_index]


def _require(raw: dict, key: str, where: str):
    """Get a required field from a dict, raising CatalogError if missing."""
    if key not in raw:
        raise CatalogError(f"{where}: missing required field {key!r}")
    return raw[key]


def _require_int(value, key: str, where: str) -> int:
    """Convert a value to int, raising CatalogError on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        raise CatalogError(f"{where}: {key!r} must be numeric, got {value!r}")


def _build_ability(raw: dict) -> Ability:
    where = f"ability {raw.get('id', '<no id>')!r}"
    ability_id = _require(raw, "id", where)
    where = f"ability {ability_id!r}"  # Update where for better error messages

    focus_cost = _require_int(_require(raw, "focus_cost", where), "focus_cost", where)
    dc_mod = _require_int(_require(raw, "dc_mod", where), "dc_mod", where)
    unlock_phase = _require_int(_require(raw, "unlock_phase", where), "unlock_phase", where)

    return Ability(
        id=ability_id,
        name=_require(raw, "name", where),
        class_id=_require(raw, "class", where),
        focus_cost=focus_cost,
        stat=_require(raw, "stat", where),
        dc_mod=dc_mod,
        unlock_phase=unlock_phase,
        target=_require(raw, "target", where),
        on_success=tuple(_require(raw, "on_success", where)),
        on_fail=tuple(_require(raw, "on_fail", where)),
        flavor=_require(raw, "flavor", where),
        fixed_roll=raw.get("fixed_roll"),
        no_crit=bool(raw.get("no_crit", False)),
        no_fumble=bool(raw.get("no_fumble", False)),
        stat_alt=raw.get("stat_alt"),
        once_per=raw.get("once_per"),
        extra_cost=raw.get("extra_cost", {}),
    )


def load_catalog(data_dir: str = "data") -> Catalog:
    base = Path(data_dir)
    classes_raw = json.loads((base / "classes.json").read_text())
    abilities_raw = json.loads((base / "abilities.json").read_text())

    classes = {}
    for cid, c in classes_raw.items():
        where = f"class {cid!r}"
        name = _require(c, "name", where)
        primary = _require(c, "primary", where)
        secondary = _require(c, "secondary", where)
        role = _require(c, "role", where)
        blurb = _require(c, "blurb", where)

        if primary not in STATS:
            raise CatalogError(f"{where}: unknown stat {primary!r}")
        if secondary not in STATS:
            raise CatalogError(f"{where}: unknown stat {secondary!r}")
        if primary == secondary:
            raise CatalogError(f"{where}: primary and secondary stat are identical")
        classes[cid] = CharacterClass(id=cid, name=name, primary=primary,
                                      secondary=secondary, role=role,
                                      blurb=blurb)

    abilities = {}
    for raw in abilities_raw:
        ability = _build_ability(raw)
        if ability.id in abilities:
            raise CatalogError(f"duplicate ability id {ability.id!r}")
        if ability.class_id not in classes:
            raise CatalogError(f"ability {ability.id!r}: unknown class {ability.class_id!r}")
        if ability.stat not in STATS:
            raise CatalogError(f"ability {ability.id!r}: unknown stat {ability.stat!r}")
        if ability.stat_alt is not None and ability.stat_alt not in STATS:
            raise CatalogError(f"ability {ability.id!r}: unknown stat_alt {ability.stat_alt!r}")
        if ability.target not in TARGETS:
            raise CatalogError(f"ability {ability.id!r}: unknown target {ability.target!r}")
        if ability.once_per not in ONCE_PER:
            raise CatalogError(f"ability {ability.id!r}: bad once_per {ability.once_per!r}")
        if not 0 <= ability.unlock_phase <= 4:
            raise CatalogError(f"ability {ability.id!r}: unlock_phase out of range")
        abilities[ability.id] = ability

    return Catalog(classes=classes, abilities=abilities)
