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


def _build_ability(raw: dict) -> Ability:
    return Ability(
        id=raw["id"], name=raw["name"], class_id=raw["class"],
        focus_cost=int(raw["focus_cost"]), stat=raw["stat"],
        dc_mod=int(raw["dc_mod"]), unlock_phase=int(raw["unlock_phase"]),
        target=raw["target"], on_success=tuple(raw["on_success"]),
        on_fail=tuple(raw["on_fail"]), flavor=raw["flavor"],
        fixed_roll=raw.get("fixed_roll"), no_crit=bool(raw.get("no_crit", False)),
        no_fumble=bool(raw.get("no_fumble", False)), stat_alt=raw.get("stat_alt"),
        once_per=raw.get("once_per"), extra_cost=raw.get("extra_cost", {}),
    )


def load_catalog(data_dir: str = "data") -> Catalog:
    base = Path(data_dir)
    classes_raw = json.loads((base / "classes.json").read_text())
    abilities_raw = json.loads((base / "abilities.json").read_text())

    classes = {}
    for cid, c in classes_raw.items():
        for key in ("primary", "secondary"):
            if c[key] not in STATS:
                raise CatalogError(f"class {cid}: unknown stat {c[key]!r}")
        if c["primary"] == c["secondary"]:
            raise CatalogError(f"class {cid}: primary and secondary stat are identical")
        classes[cid] = CharacterClass(id=cid, name=c["name"], primary=c["primary"],
                                      secondary=c["secondary"], role=c["role"],
                                      blurb=c["blurb"])

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
