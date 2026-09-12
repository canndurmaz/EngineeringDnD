# Critical Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a LAN-multiplayer, turn-based engineering RPG where a party of nine
engineering classes designs a complex system across five project phases, narrated by a
locally-served Llama 3.2 1B model.

**Architecture:** A pure-function rules engine (`engine/`) that knows nothing about
Flask, SQLite, or the LLM, sitting under a thin orchestration layer (`service.py`) that
owns per-room locks and persistence. One SQLite database per room, with an append-only
`events` table that doubles as the SSE stream. Narration is fully decoupled: actions
resolve and commit in milliseconds, and prose streams in afterwards from a single
background worker thread.

**Tech Stack:** Python 3.12, Flask 3, SQLite (WAL), `llama-cpp-python`, `segno` (QR),
`pytest`, vanilla HTML/CSS/JS with `EventSource`. No build step, no npm, no second daemon.

**Spec:** `docs/superpowers/specs/2026-09-12-engineering-dnd-design.md`

## Global Constraints

- **Python 3.12**, standard library `sqlite3`. No ORM.
- **`engine/` must remain pure**: no imports of `flask`, `sqlite3`, `llama_cpp`, `os`,
  `time`, or `random` (randomness only via an injected `Dice`). A test enforces this.
- **No second daemon.** No Redis, Celery, RQ, eventlet, or gevent.
- **No frontend build step.** No npm, no bundler, no framework, no CDN (the LAN may be
  offline). All assets are local files under `static/`.
- **`llama-cpp-python` is an optional dependency** in `requirements-llm.txt`. The game
  must run and pass its full test suite with the package absent.
- **The test suite never invokes the real LLM.** Narration is injected behind a
  `Narrator` protocol with a deterministic fake.
- **Nothing the model emits may change game state.** The narrator renders committed rows.
- **Six stats, exact names:** `RIGOR`, `INTUITION`, `CRAFT`, `SYSTEMS`, `COMMS`, `GRIT`.
- **Five phases, exact ids and order:** `requirements`, `design`, `prototype`,
  `integration`, `qualification`.
- **Starting party resources:** Budget `100`, Schedule `100`, Technical Debt `0`.
- **Technical Debt penalty:** `+1` to all hazard DCs per full `10` points.
- **Stat modifier:** `(score - 10) // 2`. **Stamina:** `8 + GRIT_mod + (2 * level)`.
  **Focus:** `4 + max(RIGOR_mod, SYSTEMS_mod)`.
- **Nine classes, four abilities each** (36 total), two unlocked at `unlock_phase: 0`.
- **Model:** `Llama-3.2-1B-Instruct-Q4_K_M.gguf`, default source the ungated mirror
  `bartowski/Llama-3.2-1B-Instruct-GGUF`; prefer `meta-llama/Llama-3.2-1B-Instruct`
  only when `HF_TOKEN` is set.
- **Commit after every task.** Conventional commit prefixes (`feat:`, `test:`, `chore:`).

### Deviation from the spec's code layout

The spec's §7 layout has routes in `app.py` calling `engine/` directly. This plan adds
one module the spec does not name: **`service.py`**, the orchestration layer that holds
per-room locks and mediates between the pure engine and storage. Without it, either
`engine/` stops being pure or `app.py` grows to hold locking, persistence, and routing
at once. Everything else follows §7 exactly.

---

## File Structure

| File | Responsibility |
|---|---|
| `engine/dice.py` | Seeded RNG, dice-expression evaluation |
| `engine/classes.py` | `Ability` / `CharacterClass` dataclasses, `Catalog` loader + validation |
| `engine/effects.py` | Effect-verb interpreter (the 10 verbs) |
| `engine/rules.py` | Stat mods, effective DC, `resolve_action`, crit/fumble |
| `engine/character.py` | Stat rolling, character creation, level up |
| `engine/phases.py` | Phase order, campaign build, win/lose checks, phase advance |
| `data/classes.json` | Nine classes |
| `data/abilities.json` | All 36 abilities |
| `data/hazard_templates.json` | Deterministic hazard fallbacks per phase |
| `data/archetypes.json` | The eight system archetypes |
| `storage/schema.sql` | Per-room DDL |
| `storage/room_db.py` | One room's database: state load/save, event append/tail |
| `storage/index_db.py` | Lobby registry cache + `rebuild_index()` |
| `service.py` | Per-room locks, orchestration of engine + storage + narration queue |
| `narrator/base.py` | `Narrator` protocol, `NarrationRequest`, `FakeNarrator` |
| `narrator/fallback.py` | Template narrator |
| `narrator/filters.py` | Post-filter stripping invented numbers |
| `narrator/queue_.py` | Priority queue of narration jobs (trailing underscore: never shadow stdlib `queue`) |
| `narrator/worker.py` | Single background worker thread |
| `narrator/prompts.py` | Prompt construction for the three job types |
| `narrator/llm.py` | `llama-cpp-python` binding, lazy load, timeout |
| `narrator/genesis.py` | Room premise + full-campaign hazard generation |
| `app.py` | Flask app factory, HTML routes, JSON API, SSE endpoint |
| `static/css/theme.css` | Design tokens, layout, components |
| `static/js/game.js` | `EventSource` handling, DOM updates |
| `templates/*.html` | Lobby, character select, game table |
| `scripts/setup_model.py` | Idempotent GGUF download |

---

## Task 1: Project scaffolding and the dice engine

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `requirements-llm.txt`, `Makefile`, `.gitignore`
- Create: `engine/__init__.py`, `engine/dice.py`
- Test: `tests/engine/test_dice.py`, `tests/test_purity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Dice(seed: int)` with `.d20() -> int`, `.roll(expr: str | int, mods: dict[str, int]) -> int`,
  `.randint(a: int, b: int) -> int`, `.choice(seq: Sequence[T]) -> T`, `.shuffle(seq: list) -> None`,
  and `.state() -> tuple` / `.restore(state: tuple) -> None` for persisting RNG position.

- [ ] **Step 1: Create the project skeleton**

```bash
mkdir -p engine storage narrator data static/css static/js templates scripts \
         tests/engine tests/storage tests/api tests/narrator
touch engine/__init__.py storage/__init__.py narrator/__init__.py scripts/__init__.py
# tests/ must be a package: later tests import fixtures from earlier ones
# (e.g. `from tests.engine.test_effects import make_state`).
touch tests/__init__.py tests/engine/__init__.py tests/storage/__init__.py \
      tests/api/__init__.py tests/narrator/__init__.py
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-q"
```

```
# requirements.txt
Flask>=3.0
segno>=1.6
huggingface_hub>=0.24
pytest>=8.0
```

```
# requirements-llm.txt
# Optional. The game runs and the full test suite passes without this.
llama-cpp-python>=0.3.2
```

```
# .gitignore
__pycache__/
*.pyc
.pytest_cache/
models/
rooms/
instance/
venv/
```

```makefile
# Makefile
.PHONY: install install-llm setup test run
install:        ; pip install -r requirements.txt
install-llm:    ; pip install -r requirements-llm.txt
setup:          ; python scripts/setup_model.py
test:           ; pytest
run:            ; python app.py
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/engine/test_dice.py
import pytest
from engine.dice import Dice


def test_same_seed_produces_same_sequence():
    a, b = Dice(1234), Dice(1234)
    assert [a.d20() for _ in range(20)] == [b.d20() for _ in range(20)]


def test_different_seeds_diverge():
    a, b = Dice(1), Dice(2)
    assert [a.d20() for _ in range(20)] != [b.d20() for _ in range(20)]


def test_d20_stays_in_range():
    d = Dice(7)
    assert all(1 <= d.d20() <= 20 for _ in range(1000))


def test_roll_accepts_flat_integer_string():
    assert Dice(1).roll("10", {}) == 10


def test_roll_accepts_bare_int():
    assert Dice(1).roll(10, {}) == 10


def test_roll_accepts_negative_flat():
    assert Dice(1).roll("-3", {}) == -3


def test_roll_evaluates_dice_plus_stat():
    d = Dice(99)
    values = [d.roll("2d6+RIGOR", {"RIGOR": 3}) for _ in range(200)]
    assert min(values) >= 5 and max(values) <= 15


def test_roll_evaluates_dice_minus_flat():
    d = Dice(5)
    values = [d.roll("1d4-1", {}) for _ in range(200)]
    assert min(values) >= 0 and max(values) <= 3


def test_roll_with_unknown_stat_raises():
    with pytest.raises(KeyError):
        Dice(1).roll("1d6+MAGIC", {"RIGOR": 2})


def test_roll_with_garbage_expression_raises():
    with pytest.raises(ValueError):
        Dice(1).roll("two sixes", {})


def test_state_round_trip_resumes_the_sequence():
    d = Dice(42)
    [d.d20() for _ in range(5)]
    saved = d.state()
    expected = [d.d20() for _ in range(5)]
    d.restore(saved)
    assert [d.d20() for _ in range(5)] == expected
```

```python
# tests/test_purity.py
"""engine/ must stay free of framework, database, and model dependencies.

Reading the JSON data files is allowed; importing Flask, sqlite3, llama_cpp, or the
bare stdlib RNG is not. That is what keeps the whole ruleset unit-testable in isolation.
"""
import ast
import pathlib

FORBIDDEN = {"flask", "sqlite3", "llama_cpp", "random", "os", "time", "requests"}


def _imported_roots(path):
    tree = ast.parse(path.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_engine_imports_no_io_modules():
    offenders = {}
    for path in pathlib.Path("engine").rglob("*.py"):
        bad = _imported_roots(path) & FORBIDDEN
        if bad:
            offenders[str(path)] = sorted(bad)
    assert offenders == {}, f"engine/ must stay pure, found: {offenders}"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/engine/test_dice.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.dice'`

- [ ] **Step 4: Implement the dice engine**

```python
# engine/dice.py
"""Seeded randomness. The single source of chance in the whole engine."""
from __future__ import annotations

import re
from typing import Sequence, TypeVar

# _random is the one place the engine touches the stdlib RNG; tests/test_purity.py
# allows it here by name because Dice is the injected seam everything else uses.
from random import Random as _Random

T = TypeVar("T")

_EXPR = re.compile(r"^(?:(\d+)d(\d+))?(?:([+-])([A-Za-z_]+|\d+))?$")


class Dice:
    """Deterministic dice. Construct with a seed; identical seeds replay identically."""

    def __init__(self, seed: int) -> None:
        self._rng = _Random(seed)
        self.seed = seed

    def d20(self) -> int:
        return self._rng.randint(1, 20)

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def choice(self, seq: Sequence[T]) -> T:
        return self._rng.choice(seq)

    def shuffle(self, seq: list) -> None:
        self._rng.shuffle(seq)

    def roll(self, expr: "str | int", mods: "dict[str, int] | None" = None) -> int:
        """Evaluate a dice expression: '2d6+RIGOR', '1d4-1', '10', or a bare int."""
        mods = mods or {}
        if isinstance(expr, int):
            return expr
        text = str(expr).replace(" ", "")
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        match = _EXPR.fullmatch(text)
        if not match or text == "":
            raise ValueError(f"bad dice expression: {expr!r}")
        count, faces, sign, term = match.groups()
        total = 0
        if count:
            total = sum(self._rng.randint(1, int(faces)) for _ in range(int(count)))
        if term is not None:
            value = int(term) if term.isdigit() else mods[term]
            total += value if sign == "+" else -value
        return total

    def state(self) -> tuple:
        return self._rng.getstate()

    def restore(self, state: tuple) -> None:
        self._rng.setstate(state)
```

Note: `engine/dice.py` imports `random` deliberately — it is the injected seam. Add
`dice.py` to the purity test's exclusions:

```python
# tests/test_purity.py — adjust the loop in test_engine_imports_no_io_modules
    for path in pathlib.Path("engine").rglob("*.py"):
        if path.as_posix() == "engine/dice.py":
            continue  # the one sanctioned RNG seam, matched by full path
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/ -v`
Expected: PASS — 11 dice tests plus the purity test.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml requirements.txt requirements-llm.txt Makefile .gitignore engine/ storage/ narrator/ tests/
git commit -m "feat: project scaffolding and seeded dice engine"
```

---

## Task 2: Class and ability catalog

**Files:**
- Create: `engine/classes.py`, `data/classes.json`
- Create: `data/abilities.json` (four abilities only; Task 3 completes the roster)
- Test: `tests/engine/test_classes.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `STATS: tuple[str, ...]` — the six stat names in canonical order.
  - `Ability` frozen dataclass: `id, name, class_id, focus_cost, stat, dc_mod, unlock_phase,
    target, on_success, on_fail, flavor, fixed_roll, no_crit, no_fumble, stat_alt,
    once_per, extra_cost`.
  - `CharacterClass` frozen dataclass: `id, name, primary, secondary, role, blurb`.
  - `Catalog` with `.classes: dict[str, CharacterClass]`, `.abilities: dict[str, Ability]`,
    `.abilities_for(class_id) -> list[Ability]`,
    `.unlocked_for(class_id, phase_index) -> list[Ability]`.
  - `load_catalog(data_dir: str = "data") -> Catalog`.
  - `CatalogError(Exception)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_classes.py
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/engine/test_classes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.classes'`

- [ ] **Step 3: Write the class data**

```json
{
  "mechanical_engineer": {
    "name": "Mechanical Engineer", "primary": "CRAFT", "secondary": "RIGOR",
    "role": "Structural damage and party shielding",
    "blurb": "Owns the load paths, the thermal budget, and every argument about tolerances."
  },
  "computer_scientist": {
    "name": "Computer Scientist", "primary": "RIGOR", "secondary": "INTUITION",
    "role": "Burst damage and Technical Debt removal",
    "blurb": "Can halve any problem in half the time, and will tell you the complexity class while doing it."
  },
  "ee_engineer": {
    "name": "Electrical & Electronics Engineer", "primary": "RIGOR", "secondary": "CRAFT",
    "role": "Reveal, resist, and expensive respins",
    "blurb": "Knows where the return current actually goes. Nobody else does."
  },
  "system_engineer": {
    "name": "System Engineer", "primary": "SYSTEMS", "secondary": "COMMS",
    "role": "Force multiplier; lowers difficulty for everyone",
    "blurb": "Wins not by rolling well but by changing what everyone else rolls against."
  },
  "product_manager": {
    "name": "Product Manager", "primary": "COMMS", "secondary": "SYSTEMS",
    "role": "Resource manipulation; trades debt for speed",
    "blurb": "Can make any problem disappear. It comes back in the next phase, larger."
  },
  "mechanical_technician": {
    "name": "Mechanical Technician", "primary": "CRAFT", "secondary": "GRIT",
    "role": "Cheap, reliable, never wholly fails",
    "blurb": "Has fixed it before, has the fixture for it, and is not impressed by your CAD model."
  },
  "electrical_technician": {
    "name": "Electrical Technician", "primary": "CRAFT", "secondary": "GRIT",
    "role": "Exposes weaknesses and enables allies",
    "blurb": "Finds the fault in four minutes with a multimeter and an unkind expression."
  },
  "control_systems_engineer": {
    "name": "Control Systems Engineer", "primary": "RIGOR", "secondary": "SYSTEMS",
    "role": "Variance reduction; removes bad luck",
    "blurb": "Does not gamble. Estimates, then corrects."
  },
  "mechatronics_engineer": {
    "name": "Mechatronics Engineer", "primary": "SYSTEMS", "secondary": "CRAFT",
    "role": "Generalist; borrows from every domain",
    "blurb": "Fluent in three disciplines and trusted by none of them."
  }
}
```

Write that to `data/classes.json`. Then a four-ability starter file so the loader has
something to validate — Task 3 replaces it with the full roster:

```json
[
  {"id": "descope", "name": "Descope", "class": "product_manager", "focus_cost": 1,
   "stat": "COMMS", "dc_mod": -2, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"damage_hazard": "10"}, {"party": {"tech_debt": 3}}],
   "on_fail": [{"party": {"tech_debt": 1}}],
   "flavor": "You quietly move it to Phase 2."},
  {"id": "stakeholder_charm", "name": "Stakeholder Charm", "class": "product_manager",
   "focus_cost": 1, "stat": "COMMS", "dc_mod": 0, "unlock_phase": 0, "target": "party",
   "on_success": [{"party": {"budget": 15}}], "on_fail": [{"party": {"budget": -5}}],
   "flavor": "Nobody is quite sure what was agreed, but everyone feels good."},
  {"id": "roadmap_rally", "name": "Roadmap Rally", "class": "product_manager",
   "focus_cost": 2, "stat": "COMMS", "dc_mod": 1, "unlock_phase": 2, "target": "party",
   "on_success": [{"restore_focus": {"scope": "party", "amount": "2"}}],
   "on_fail": [], "flavor": "The slides are genuinely quite good."},
  {"id": "reprioritize", "name": "Reprioritize", "class": "product_manager",
   "focus_cost": 3, "stat": "SYSTEMS", "dc_mod": 3, "unlock_phase": 3, "target": "hazard",
   "on_success": [{"skip_hazard": {"return_multiplier": 1.5}}],
   "on_fail": [{"party": {"schedule": -3}}],
   "flavor": "It is not gone. It is later, and angrier."}
]
```

- [ ] **Step 4: Implement the catalog**

```python
# engine/classes.py
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
```

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/engine/test_classes.py -v`
Expected: PASS — 9 tests.

- [ ] **Step 6: Commit**

```bash
git add engine/classes.py data/classes.json data/abilities.json tests/engine/test_classes.py
git commit -m "feat: class and ability catalog with JSON validation"
```

---

## Task 3: The complete 36-ability roster

**Files:**
- Modify: `data/abilities.json` (replace with all 36)
- Test: `tests/engine/test_roster.py`

**Interfaces:**
- Consumes: `load_catalog`, `Catalog`, `STATS` from Task 2.
- Produces: a complete, validated `data/abilities.json`. No new Python interfaces.

Encode every ability from spec §3.1. The completeness tests below are what guarantee
nothing is skipped — they fail loudly on a partial roster, so there is no way to leave
this half-done.

- [ ] **Step 1: Write the failing completeness tests**

```python
# tests/engine/test_roster.py
import pytest
from engine.classes import STATS, load_catalog

EXPECTED_ABILITY_IDS = {
    "mechanical_engineer": {"fea_deep_dive", "design_margin", "tolerance_stack_up", "thermal_sink"},
    "computer_scientist": {"binary_search_debug", "unit_test_barrage", "refactor", "rubber_duck"},
    "ee_engineer": {"signal_integrity_scan", "emi_shield", "power_budget", "board_respin"},
    "system_engineer": {"requirements_trace", "trade_study", "icd_lockdown", "vv_sweep"},
    "product_manager": {"descope", "stakeholder_charm", "roadmap_rally", "reprioritize"},
    "mechanical_technician": {"shop_floor_fix", "jig_and_fixture", "torque_to_spec", "scavenge_parts"},
    "electrical_technician": {"continuity_check", "solder_bodge", "harness_rework", "instrumentation_setup"},
    "control_systems_engineer": {"kalman_filter", "pid_tune", "stability_margin", "model_in_the_loop"},
    "mechatronics_engineer": {"sensor_fusion", "actuator_integration", "rapid_prototype", "cross_domain_hack"},
}


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_exactly_thirty_six_abilities(cat):
    assert len(cat.abilities) == 36


def test_every_class_has_its_four_named_abilities(cat):
    for class_id, expected in EXPECTED_ABILITY_IDS.items():
        got = {a.id for a in cat.abilities_for(class_id)}
        assert got == expected, f"{class_id}: expected {expected}, got {got}"


def test_every_class_has_exactly_two_starting_abilities(cat):
    for class_id in EXPECTED_ABILITY_IDS:
        starters = [a for a in cat.abilities_for(class_id) if a.unlock_phase == 0]
        assert len(starters) == 2, f"{class_id} has {len(starters)} starters"


def test_focus_costs_are_in_the_zero_to_three_band(cat):
    for a in cat.abilities.values():
        assert 0 <= a.focus_cost <= 3, f"{a.id} costs {a.focus_cost}"


def test_every_ability_has_at_least_one_success_effect_or_a_special_rule(cat):
    for a in cat.abilities.values():
        special = a.fixed_roll is not None or a.no_crit or a.no_fumble
        assert a.on_success or special, f"{a.id} does nothing on success"


def test_every_ability_has_nonempty_flavor(cat):
    for a in cat.abilities.values():
        assert a.flavor.strip(), f"{a.id} has no flavor text"


def test_kalman_filter_is_a_flat_eleven_with_no_swing(cat):
    k = cat.abilities["kalman_filter"]
    assert k.fixed_roll == 11 and k.no_crit and k.no_fumble


def test_unit_test_barrage_cannot_fumble(cat):
    assert cat.abilities["unit_test_barrage"].no_fumble


def test_board_respin_costs_budget(cat):
    assert cat.abilities["board_respin"].extra_cost.get("budget") == 15


def test_debt_adding_abilities_actually_add_debt(cat):
    for ability_id in ("descope", "solder_bodge", "scavenge_parts", "rapid_prototype"):
        effects = cat.abilities[ability_id].on_success
        assert any("party" in e and e["party"].get("tech_debt", 0) > 0 for e in effects), \
            f"{ability_id} should add Technical Debt"


def test_binary_search_debug_is_once_per_hazard(cat):
    assert cat.abilities["binary_search_debug"].once_per == "hazard"


def test_cross_domain_hack_is_once_per_phase(cat):
    assert cat.abilities["cross_domain_hack"].once_per == "phase"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/engine/test_roster.py -v`
Expected: FAIL — `assert 4 == 36` on the first test.

- [ ] **Step 3: Write the full roster**

Replace `data/abilities.json` with all 36. Keep the four Product Manager entries from
Task 2 verbatim and add the other 32. Every entry uses the Task 2 schema. The complete
file:

```json
[
  {"id": "fea_deep_dive", "name": "FEA Deep Dive", "class": "mechanical_engineer", "focus_cost": 2, "stat": "RIGOR", "dc_mod": 1, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"damage_hazard": "3d6+RIGOR"}], "on_fail": [{"party": {"schedule": -2}}],
   "flavor": "Six hours of meshing, and the stress concentration is exactly where you said it was."},
  {"id": "design_margin", "name": "Design Margin", "class": "mechanical_engineer", "focus_cost": 2, "stat": "CRAFT", "dc_mod": 0, "unlock_phase": 0, "target": "party",
   "on_success": [{"shield": {"scope": "party", "amount": "6", "rounds": 1}}], "on_fail": [],
   "flavor": "You quietly specify the next size up and tell nobody."},
  {"id": "tolerance_stack_up", "name": "Tolerance Stack-Up", "class": "mechanical_engineer", "focus_cost": 1, "stat": "RIGOR", "dc_mod": 0, "unlock_phase": 2, "target": "hazard",
   "on_success": [{"damage_hazard": "1d8+RIGOR"}, {"reveal": {"what": "weakness"}}], "on_fail": [],
   "flavor": "Worst case, everything is out by 0.4 mm in the same direction."},
  {"id": "thermal_sink", "name": "Thermal Sink", "class": "mechanical_engineer", "focus_cost": 1, "stat": "CRAFT", "dc_mod": -1, "unlock_phase": 3, "target": "ally",
   "on_success": [{"shield": {"scope": "ally", "amount": "8", "rounds": 2}}], "on_fail": [],
   "flavor": "Aluminium, mass, and an honest admission that fans are cheating."},

  {"id": "binary_search_debug", "name": "Binary Search Debug", "class": "computer_scientist", "focus_cost": 3, "stat": "INTUITION", "dc_mod": 2, "unlock_phase": 0, "target": "hazard", "once_per": "hazard",
   "on_success": [{"damage_hazard": {"fraction": 0.5}}], "on_fail": [{"party": {"schedule": -1}}],
   "flavor": "Halve it. Halve it again. There it is, on line 4,118."},
  {"id": "unit_test_barrage", "name": "Unit Test Barrage", "class": "computer_scientist", "focus_cost": 1, "stat": "RIGOR", "dc_mod": -1, "unlock_phase": 0, "target": "hazard", "no_fumble": true,
   "on_success": [{"damage_hazard": "2d4+RIGOR"}], "on_fail": [],
   "flavor": "Four hundred assertions. Three of them matter."},
  {"id": "refactor", "name": "Refactor", "class": "computer_scientist", "focus_cost": 2, "stat": "RIGOR", "dc_mod": 1, "unlock_phase": 2, "target": "party",
   "on_success": [{"party": {"tech_debt": -5}}], "on_fail": [{"party": {"schedule": -2}}],
   "flavor": "No new features. Everyone is furious. It was the right call."},
  {"id": "rubber_duck", "name": "Rubber Duck", "class": "computer_scientist", "focus_cost": 0, "stat": "COMMS", "dc_mod": -3, "unlock_phase": 3, "target": "ally",
   "on_success": [{"restore_focus": {"scope": "ally", "amount": "2"}}], "on_fail": [],
   "flavor": "You explain it aloud and solve it in the third sentence."},

  {"id": "signal_integrity_scan", "name": "Signal Integrity Scan", "class": "ee_engineer", "focus_cost": 0, "stat": "RIGOR", "dc_mod": -2, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"reveal": {"what": "all"}}, {"apply_condition": {"scope": "party", "condition": "roll_bonus", "value": 2, "rounds": 3}}], "on_fail": [],
   "flavor": "The scope tells you the truth the schematic was hiding."},
  {"id": "emi_shield", "name": "EMI Shield", "class": "ee_engineer", "focus_cost": 1, "stat": "CRAFT", "dc_mod": 0, "unlock_phase": 0, "target": "party",
   "on_success": [{"apply_condition": {"scope": "party", "condition": "resist_burn", "value": 1, "rounds": 2}}], "on_fail": [],
   "flavor": "Copper tape, a ground stitch, and an end to the mystery resets."},
  {"id": "power_budget", "name": "Power Budget", "class": "ee_engineer", "focus_cost": 1, "stat": "SYSTEMS", "dc_mod": 0, "unlock_phase": 2, "target": "hazard",
   "on_success": [{"damage_hazard": {"per_party_focus": 2}}], "on_fail": [],
   "flavor": "Every milliamp accounted for, on one spreadsheet, finally."},
  {"id": "board_respin", "name": "Board Respin", "class": "ee_engineer", "focus_cost": 2, "stat": "CRAFT", "dc_mod": -1, "unlock_phase": 4, "target": "hazard", "extra_cost": {"budget": 15},
   "on_success": [{"damage_hazard": "4d6+CRAFT"}], "on_fail": [{"party": {"schedule": -4}}],
   "flavor": "Rev C. It will work. It has to, there is no money for Rev D."},

  {"id": "requirements_trace", "name": "Requirements Trace", "class": "system_engineer", "focus_cost": 2, "stat": "SYSTEMS", "dc_mod": 1, "unlock_phase": 0, "target": "party",
   "on_success": [{"apply_condition": {"scope": "party", "condition": "dc_delta", "value": -3, "rounds": 1}}], "on_fail": [],
   "flavor": "Every line in the spec now points at a test. The fog lifts."},
  {"id": "trade_study", "name": "Trade Study", "class": "system_engineer", "focus_cost": 1, "stat": "RIGOR", "dc_mod": 0, "unlock_phase": 0, "target": "ally",
   "on_success": [{"reroll_grant": {"scope": "ally", "count": 1}}], "on_fail": [{"party": {"schedule": -1}}],
   "flavor": "Four options, one matrix, and a decision nobody can argue with later."},
  {"id": "icd_lockdown", "name": "ICD Lockdown", "class": "system_engineer", "focus_cost": 2, "stat": "SYSTEMS", "dc_mod": 1, "unlock_phase": 2, "target": "hazard",
   "on_success": [{"apply_condition": {"scope": "hazard", "condition": "no_debt", "value": 1, "rounds": 2}}], "on_fail": [],
   "flavor": "The interface is frozen. Anyone who wants to change it may file a change request."},
  {"id": "vv_sweep", "name": "V&V Sweep", "class": "system_engineer", "focus_cost": 2, "stat": "RIGOR", "dc_mod": 0, "unlock_phase": 3, "target": "hazard",
   "on_success": [{"damage_hazard": "2d6+SYSTEMS"}, {"apply_condition": {"scope": "hazard", "condition": "stunned", "value": 1, "rounds": 1}}], "on_fail": [],
   "flavor": "Verification, then validation. They are not the same word and you will die on this hill."},

  {"id": "descope", "name": "Descope", "class": "product_manager", "focus_cost": 1, "stat": "COMMS", "dc_mod": -2, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"damage_hazard": "10"}, {"party": {"tech_debt": 3}}], "on_fail": [{"party": {"tech_debt": 1}}],
   "flavor": "You quietly move it to Phase 2."},
  {"id": "stakeholder_charm", "name": "Stakeholder Charm", "class": "product_manager", "focus_cost": 1, "stat": "COMMS", "dc_mod": 0, "unlock_phase": 0, "target": "party",
   "on_success": [{"party": {"budget": 15}}], "on_fail": [{"party": {"budget": -5}}],
   "flavor": "Nobody is quite sure what was agreed, but everyone feels good."},
  {"id": "roadmap_rally", "name": "Roadmap Rally", "class": "product_manager", "focus_cost": 2, "stat": "COMMS", "dc_mod": 1, "unlock_phase": 2, "target": "party",
   "on_success": [{"restore_focus": {"scope": "party", "amount": "2"}}], "on_fail": [],
   "flavor": "The slides are genuinely quite good."},
  {"id": "reprioritize", "name": "Reprioritize", "class": "product_manager", "focus_cost": 3, "stat": "SYSTEMS", "dc_mod": 3, "unlock_phase": 3, "target": "hazard",
   "on_success": [{"skip_hazard": {"return_multiplier": 1.5}}], "on_fail": [{"party": {"schedule": -3}}],
   "flavor": "It is not gone. It is later, and angrier."},

  {"id": "shop_floor_fix", "name": "Shop Floor Fix", "class": "mechanical_technician", "focus_cost": 0, "stat": "CRAFT", "dc_mod": -2, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"damage_hazard": "1d6+CRAFT"}], "on_fail": [{"damage_hazard": "2"}],
   "flavor": "Seen it before. Fixed it before. Did not write it down either time."},
  {"id": "jig_and_fixture", "name": "Jig & Fixture", "class": "mechanical_technician", "focus_cost": 1, "stat": "CRAFT", "dc_mod": 0, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"apply_condition": {"scope": "party", "condition": "roll_bonus", "value": 3, "rounds": 3}}], "on_fail": [],
   "flavor": "Two hours building the tool. Ten minutes doing the job. Correct order."},
  {"id": "torque_to_spec", "name": "Torque to Spec", "class": "mechanical_technician", "focus_cost": 1, "stat": "GRIT", "dc_mod": -1, "unlock_phase": 2, "target": "hazard",
   "on_success": [{"damage_hazard": "2d6+GRIT", "double_if_weakness": "CRAFT"}], "on_fail": [],
   "flavor": "Click. Every fastener, in sequence, and the log signed."},
  {"id": "scavenge_parts", "name": "Scavenge Parts", "class": "mechanical_technician", "focus_cost": 1, "stat": "GRIT", "dc_mod": 0, "unlock_phase": 3, "target": "party",
   "on_success": [{"party": {"budget": 10, "tech_debt": 1}}], "on_fail": [],
   "flavor": "The old rig had one. The old rig does not need it any more."},

  {"id": "continuity_check", "name": "Continuity Check", "class": "electrical_technician", "focus_cost": 0, "stat": "CRAFT", "dc_mod": -2, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"reveal": {"what": "weakness"}}, {"apply_condition": {"scope": "party", "condition": "next_attack_bonus", "value": 4, "rounds": 1}}], "on_fail": [],
   "flavor": "Beep. Beep. Silence. Found it."},
  {"id": "solder_bodge", "name": "Solder Bodge", "class": "electrical_technician", "focus_cost": 1, "stat": "CRAFT", "dc_mod": -1, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"damage_hazard": "2d6+CRAFT"}, {"party": {"tech_debt": 2}}], "on_fail": [],
   "flavor": "A blue wire. Nobody will ever know. Everybody will always know."},
  {"id": "harness_rework", "name": "Harness Rework", "class": "electrical_technician", "focus_cost": 2, "stat": "CRAFT", "dc_mod": 0, "unlock_phase": 2, "target": "hazard",
   "on_success": [{"damage_hazard": "2d6+CRAFT"}, {"party": {"tech_debt": -2}}], "on_fail": [],
   "flavor": "Re-pinned, re-labelled, re-tied. It looks like someone cared."},
  {"id": "instrumentation_setup", "name": "Instrumentation Setup", "class": "electrical_technician", "focus_cost": 1, "stat": "RIGOR", "dc_mod": 0, "unlock_phase": 3, "target": "party",
   "on_success": [{"apply_condition": {"scope": "party", "condition": "crit_range", "value": 19, "rounds": 2}}], "on_fail": [],
   "flavor": "Forty channels, all calibrated. Now you can see what you are doing."},

  {"id": "kalman_filter", "name": "Kalman Filter", "class": "control_systems_engineer", "focus_cost": 2, "stat": "RIGOR", "dc_mod": 0, "unlock_phase": 0, "target": "hazard", "fixed_roll": 11, "no_crit": true, "no_fumble": true,
   "on_success": [{"damage_hazard": "2d6+RIGOR"}], "on_fail": [],
   "flavor": "You do not need a lucky measurement. You need the right estimate."},
  {"id": "pid_tune", "name": "PID Tune", "class": "control_systems_engineer", "focus_cost": 1, "stat": "RIGOR", "dc_mod": -1, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"damage_hazard": "2d6+RIGOR", "bonus_if_repeated": 0.5}], "on_fail": [],
   "flavor": "More P. Less D. There, the overshoot is gone."},
  {"id": "stability_margin", "name": "Stability Margin", "class": "control_systems_engineer", "focus_cost": 1, "stat": "SYSTEMS", "dc_mod": 0, "unlock_phase": 2, "target": "party",
   "on_success": [{"apply_condition": {"scope": "party", "condition": "cancel_fumble", "value": 1, "rounds": 3}}], "on_fail": [],
   "flavor": "Six dB and forty degrees. Room to be wrong."},
  {"id": "model_in_the_loop", "name": "Model-in-the-Loop", "class": "control_systems_engineer", "focus_cost": 2, "stat": "SYSTEMS", "dc_mod": 1, "unlock_phase": 4, "target": "hazard",
   "on_success": [{"damage_hazard": "2d8+SYSTEMS"}, {"reveal": {"what": "next_attack"}}], "on_fail": [],
   "flavor": "The plant model was right, which is both reassuring and slightly alarming."},

  {"id": "sensor_fusion", "name": "Sensor Fusion", "class": "mechatronics_engineer", "focus_cost": 1, "stat": "SYSTEMS", "dc_mod": 0, "unlock_phase": 0, "target": "hazard", "stat_alt": "CRAFT",
   "on_success": [{"damage_hazard": "2d6+SYSTEMS"}], "on_fail": [],
   "flavor": "Neither sensor is trustworthy. Together they are almost honest."},
  {"id": "actuator_integration", "name": "Actuator Integration", "class": "mechatronics_engineer", "focus_cost": 2, "stat": "CRAFT", "dc_mod": 1, "unlock_phase": 0, "target": "hazard",
   "on_success": [{"damage_hazard": "2d6+CRAFT"}, {"heal_ally": "2"}], "on_fail": [],
   "flavor": "Mechanical, electrical, and firmware agree on one thing at last: which way is positive."},
  {"id": "rapid_prototype", "name": "Rapid Prototype", "class": "mechatronics_engineer", "focus_cost": 1, "stat": "CRAFT", "dc_mod": -2, "unlock_phase": 2, "target": "hazard",
   "on_success": [{"damage_hazard": "2d6+CRAFT"}, {"party": {"tech_debt": 1}}], "on_fail": [],
   "flavor": "Printed overnight, held together with hope and M3 bolts. It answers the question."},
  {"id": "cross_domain_hack", "name": "Cross-Domain Hack", "class": "mechatronics_engineer", "focus_cost": 3, "stat": "SYSTEMS", "dc_mod": 2, "unlock_phase": 4, "target": "hazard", "once_per": "phase",
   "on_success": [{"copy_ability": {"scope": "ally"}}], "on_fail": [],
   "flavor": "You have watched all of them work. You can do a passable impression of any of them."}
]
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/engine/ -v`
Expected: PASS — all 12 roster tests plus Task 2's 9 catalog tests.

- [ ] **Step 5: Commit**

```bash
git add data/abilities.json tests/engine/test_roster.py
git commit -m "feat: complete 36-ability roster across all nine classes"
```

---

## Task 4: Effect-verb interpreter

**Files:**
- Create: `engine/effects.py`
- Test: `tests/engine/test_effects.py`

**Interfaces:**
- Consumes: `Dice` (Task 1), `Ability` (Task 2).
- Produces:
  - `VERBS: frozenset[str]` — 11 verbs. The spec's §3.2 list of 10 plus `copy_ability`,
    which the Mechatronics Engineer's Cross-Domain Hack requires.
  - `MODIFIERS: frozenset[str]` — `double_if_weakness`, `bonus_if_repeated`.
  - `EffectError(Exception)`.
  - `apply_effects(state: dict, effects: Sequence[dict], ctx: dict, dice: Dice) -> list[dict]`
    — mutates `state` in place, returns change records.
  - `add_condition(state, name, scope, value, rounds, target_id=None) -> None`.
  - `active_conditions(state, name, player_id=None) -> list[dict]`.
  - `condition_total(state, name, player_id=None) -> int`.
  - `expire_conditions(state) -> None` — decrements `rounds`, drops exhausted entries.

**State shape** (established here, used by every later task):

```python
{
  "room": {"id", "name", "premise", "archetype", "phase_index", "status", "rng_seed"},
  "party": {"budget": int, "schedule": int, "tech_debt": int},
  "characters": {player_id: {"player_id", "name", "class_id", "stats": {...}, "level",
                            "stamina", "max_stamina", "focus", "max_focus",
                            "unlocked": [ability_id], "used": {ability_id: scope_key}}},
  "hazards": [hazard_dict],            # whole campaign, all phases
  "active_hazard_id": str | None,
  "turn": {"round": int, "order": [player_id], "turn_index": int},
  "conditions": [ {"name", "scope", "value", "rounds", "target_id"} ],
}
```

A change record is `{"kind": str, ...}`; `kind` is one of `hazard_damage`,
`party_delta`, `heal`, `focus_restore`, `stress`, `condition`, `reveal`, `reroll`,
`shield`, `hazard_skipped`, `ability_borrowed`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_effects.py
import pytest
from engine.dice import Dice
from engine.effects import (EffectError, active_conditions, add_condition,
                            apply_effects, condition_total, expire_conditions)


def make_state():
    return {
        "room": {"id": "r", "phase_index": 0, "status": "active"},
        "party": {"budget": 100, "schedule": 100, "tech_debt": 0},
        "characters": {
            "p1": {"player_id": "p1", "name": "Ada", "class_id": "computer_scientist",
                   "stats": {"RIGOR": 16, "INTUITION": 14, "CRAFT": 10,
                             "SYSTEMS": 12, "COMMS": 10, "GRIT": 10},
                   "level": 1, "stamina": 10, "max_stamina": 10, "focus": 4,
                   "max_focus": 7,
                   "unlocked": ["refactor", "unit_test_barrage"], "used": {}},
            "p2": {"player_id": "p2", "name": "Ben", "class_id": "mechanical_technician",
                   "stats": {"RIGOR": 10, "INTUITION": 10, "CRAFT": 15,
                             "SYSTEMS": 10, "COMMS": 10, "GRIT": 14},
                   "level": 1, "stamina": 4, "max_stamina": 12, "focus": 2,
                   "max_focus": 4, "unlocked": ["shop_floor_fix"], "used": {}},
        },
        "hazards": [{"id": "h1", "phase_index": 0, "ordinal": 0,
                     "name": "Thermal Runaway",
                     "description": "The pack heats faster than it can shed.",
                     "severity": 30, "max_severity": 30, "dc": 12,
                     "attack_type": "stress", "weakness": "CRAFT",
                     "revealed": [], "defeated": False, "is_boss": False}],
        "active_hazard_id": "h1",
        "turn": {"round": 1, "order": ["p1", "p2"], "turn_index": 0},
        "conditions": [],
    }


def ctx(state, actor="p1", target=None, crit=False):
    mods = {k: (v - 10) // 2 for k, v in state["characters"][actor]["stats"].items()}
    return {"actor_id": actor, "target_id": target, "mods": mods, "crit": crit}


def test_damage_hazard_reduces_severity():
    s = make_state()
    apply_effects(s, [{"damage_hazard": "7"}], ctx(s), Dice(1))
    assert s["hazards"][0]["severity"] == 23


def test_damage_hazard_never_goes_below_zero():
    s = make_state()
    apply_effects(s, [{"damage_hazard": "999"}], ctx(s), Dice(1))
    assert s["hazards"][0]["severity"] == 0


def test_damage_hazard_evaluates_stat_expressions():
    s = make_state()
    changes = apply_effects(s, [{"damage_hazard": "1d1+RIGOR"}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 1 + 3  # RIGOR 16 -> +3


def test_crit_doubles_damage():
    s = make_state()
    changes = apply_effects(s, [{"damage_hazard": "7"}], ctx(s, crit=True), Dice(1))
    assert changes[0]["amount"] == 14


def test_crit_does_not_double_technical_debt():
    s = make_state()
    apply_effects(s, [{"party": {"tech_debt": 3}}], ctx(s, crit=True), Dice(1))
    assert s["party"]["tech_debt"] == 3


def test_crit_does_not_double_a_budget_penalty():
    s = make_state()
    apply_effects(s, [{"party": {"budget": -5}}], ctx(s, crit=True), Dice(1))
    assert s["party"]["budget"] == 95


def test_crit_doubles_a_budget_gain():
    s = make_state()
    apply_effects(s, [{"party": {"budget": 15}}], ctx(s, crit=True), Dice(1))
    assert s["party"]["budget"] == 130


def test_fraction_damage_halves_remaining_severity():
    s = make_state()
    s["hazards"][0]["severity"] = 21
    apply_effects(s, [{"damage_hazard": {"fraction": 0.5}}], ctx(s), Dice(1))
    assert s["hazards"][0]["severity"] == 11  # ceil-free: 21 - int(21*0.5)=21-10


def test_per_party_focus_damage_scales_with_unspent_focus():
    s = make_state()  # p1 focus 4, p2 focus 2 -> 6 total
    changes = apply_effects(s, [{"damage_hazard": {"per_party_focus": 2}}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 12


def test_double_if_weakness_applies_when_matched():
    s = make_state()  # hazard weakness is CRAFT
    changes = apply_effects(
        s, [{"damage_hazard": "5", "double_if_weakness": "CRAFT"}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 10


def test_double_if_weakness_is_inert_when_unmatched():
    s = make_state()
    changes = apply_effects(
        s, [{"damage_hazard": "5", "double_if_weakness": "COMMS"}], ctx(s), Dice(1))
    assert changes[0]["amount"] == 5


def test_party_delta_applies_multiple_fields():
    s = make_state()
    apply_effects(s, [{"party": {"budget": 10, "tech_debt": 1}}], ctx(s), Dice(1))
    assert s["party"]["budget"] == 110 and s["party"]["tech_debt"] == 1


def test_tech_debt_floors_at_zero():
    s = make_state()
    apply_effects(s, [{"party": {"tech_debt": -5}}], ctx(s), Dice(1))
    assert s["party"]["tech_debt"] == 0


def test_heal_ally_restores_stamina_without_exceeding_max():
    s = make_state()
    apply_effects(s, [{"heal_ally": "50"}], ctx(s, target="p2"), Dice(1))
    assert s["characters"]["p2"]["stamina"] == 12


def test_heal_ally_defaults_to_the_actor_when_no_target():
    s = make_state()
    s["characters"]["p1"]["stamina"] = 5
    apply_effects(s, [{"heal_ally": "3"}], ctx(s), Dice(1))
    assert s["characters"]["p1"]["stamina"] == 8


def test_restore_focus_party_scope_tops_up_everyone():
    s = make_state()
    apply_effects(s, [{"restore_focus": {"scope": "party", "amount": "2"}}],
                  ctx(s), Dice(1))
    assert s["characters"]["p1"]["focus"] == 6
    assert s["characters"]["p2"]["focus"] == 4  # capped at max_focus


def test_stress_self_damages_the_actor():
    s = make_state()
    apply_effects(s, [{"stress_self": "3"}], ctx(s), Dice(1))
    assert s["characters"]["p1"]["stamina"] == 7


def test_stamina_floors_at_zero():
    s = make_state()
    apply_effects(s, [{"stress_self": "99"}], ctx(s), Dice(1))
    assert s["characters"]["p1"]["stamina"] == 0


def test_reveal_weakness_marks_the_hazard():
    s = make_state()
    apply_effects(s, [{"reveal": {"what": "weakness"}}], ctx(s), Dice(1))
    assert "weakness" in s["hazards"][0]["revealed"]


def test_reveal_all_marks_every_field():
    s = make_state()
    apply_effects(s, [{"reveal": {"what": "all"}}], ctx(s), Dice(1))
    assert set(s["hazards"][0]["revealed"]) >= {"weakness", "dc", "next_attack"}


def test_apply_condition_registers_a_party_condition():
    s = make_state()
    apply_effects(s, [{"apply_condition": {"scope": "party", "condition": "dc_delta",
                                           "value": -3, "rounds": 1}}], ctx(s), Dice(1))
    assert condition_total(s, "dc_delta", "p2") == -3


def test_conditions_stack_additively():
    s = make_state()
    add_condition(s, "roll_bonus", "party", 2, 3)
    add_condition(s, "roll_bonus", "party", 3, 3)
    assert condition_total(s, "roll_bonus", "p1") == 5


def test_ally_scoped_condition_does_not_reach_other_players():
    s = make_state()
    add_condition(s, "roll_bonus", "ally", 4, 1, target_id="p2")
    assert condition_total(s, "roll_bonus", "p2") == 4
    assert condition_total(s, "roll_bonus", "p1") == 0


def test_expire_conditions_decrements_and_drops():
    s = make_state()
    add_condition(s, "roll_bonus", "party", 2, 1)
    expire_conditions(s)
    assert active_conditions(s, "roll_bonus") == []


def test_shield_registers_absorbing_condition():
    s = make_state()
    apply_effects(s, [{"shield": {"scope": "party", "amount": "6", "rounds": 1}}],
                  ctx(s), Dice(1))
    assert condition_total(s, "shield", "p1") == 6


def test_reroll_grant_registers_for_the_target():
    s = make_state()
    apply_effects(s, [{"reroll_grant": {"scope": "ally", "count": 1}}],
                  ctx(s, target="p2"), Dice(1))
    assert condition_total(s, "reroll", "p2") == 1


def test_skip_hazard_defeats_it_and_queues_a_larger_return():
    s = make_state()
    apply_effects(s, [{"skip_hazard": {"return_multiplier": 1.5}}], ctx(s), Dice(1))
    assert s["hazards"][0]["defeated"] is True
    returned = [h for h in s["hazards"] if h["id"] != "h1"]
    assert len(returned) == 1
    assert returned[0]["max_severity"] == 45
    assert returned[0]["phase_index"] == 1


def test_copy_ability_grants_a_borrow_condition():
    s = make_state()
    apply_effects(s, [{"copy_ability": {"scope": "ally"}}], ctx(s), Dice(1))
    assert condition_total(s, "borrowed_ability", "p1") == 1


def test_unknown_verb_raises():
    s = make_state()
    with pytest.raises(EffectError, match="unknown"):
        apply_effects(s, [{"summon_dragon": "1"}], ctx(s), Dice(1))


def test_effect_with_two_verbs_raises():
    s = make_state()
    with pytest.raises(EffectError, match="exactly one"):
        apply_effects(s, [{"damage_hazard": "1", "heal_ally": "1"}], ctx(s), Dice(1))
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/engine/test_effects.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.effects'`

- [ ] **Step 3: Implement the interpreter**

```python
# engine/effects.py
"""Interpreter for the declarative effect verbs used in data/abilities.json."""
from __future__ import annotations

import math
from typing import Sequence

from engine.dice import Dice

VERBS = frozenset({
    "damage_hazard", "heal_ally", "restore_focus", "party", "stress_self",
    "apply_condition", "reveal", "reroll_grant", "shield", "skip_hazard",
    "copy_ability",
})
MODIFIERS = frozenset({"double_if_weakness", "bonus_if_repeated"})

# Crit doubles beneficial numbers only. Penalties are never amplified by good luck.
_PENALTY_FIELDS = {"tech_debt"}


class EffectError(Exception):
    """Raised when an effect dict is malformed or names an unknown verb."""


# --- conditions -------------------------------------------------------------

def add_condition(state, name, scope, value, rounds, target_id=None) -> None:
    state.setdefault("conditions", []).append({
        "name": name, "scope": scope, "value": value,
        "rounds": rounds, "target_id": target_id,
    })


def active_conditions(state, name, player_id=None) -> list:
    out = []
    for c in state.get("conditions", []):
        if c["name"] != name:
            continue
        if c["scope"] == "party" or player_id is None or c["target_id"] == player_id:
            out.append(c)
    return out


def condition_total(state, name, player_id=None) -> int:
    return sum(c["value"] for c in active_conditions(state, name, player_id))


def expire_conditions(state) -> None:
    remaining = []
    for c in state.get("conditions", []):
        c["rounds"] -= 1
        if c["rounds"] > 0:
            remaining.append(c)
    state["conditions"] = remaining


# --- helpers ----------------------------------------------------------------

def _hazard(state):
    hid = state.get("active_hazard_id")
    for h in state["hazards"]:
        if h["id"] == hid:
            return h
    return None


def _verb_of(effect: dict) -> str:
    verbs = set(effect) & VERBS
    unknown = set(effect) - VERBS - MODIFIERS
    if unknown:
        raise EffectError(f"unknown effect key(s): {sorted(unknown)}")
    if len(verbs) != 1:
        raise EffectError(f"effect must contain exactly one verb, got {sorted(verbs)}")
    return verbs.pop()


def _amount(value, ctx, dice) -> int:
    return dice.roll(value, ctx["mods"])


# --- verb handlers ----------------------------------------------------------

def _do_damage_hazard(state, effect, ctx, dice, changes):
    hazard = _hazard(state)
    if hazard is None:
        return
    spec = effect["damage_hazard"]
    if isinstance(spec, dict):
        if "fraction" in spec:
            amount = int(hazard["severity"] * spec["fraction"])
        elif "per_party_focus" in spec:
            pool = sum(c["focus"] for c in state["characters"].values())
            amount = pool * int(spec["per_party_focus"])
        else:
            raise EffectError(f"unsupported damage_hazard form: {sorted(spec)}")
    else:
        amount = _amount(spec, ctx, dice)

    weakness_stat = effect.get("double_if_weakness")
    if weakness_stat and hazard.get("weakness") == weakness_stat:
        amount *= 2
    if effect.get("bonus_if_repeated") and state.get("last_ability_id") == \
            (ctx.get("ability").id if ctx.get("ability") else None):
        amount = int(amount * (1 + effect["bonus_if_repeated"]))
    if ctx.get("crit"):
        amount *= 2

    amount = max(0, amount)
    hazard["severity"] = max(0, hazard["severity"] - amount)
    if hazard["severity"] == 0:
        hazard["defeated"] = True
    changes.append({"kind": "hazard_damage", "hazard_id": hazard["id"], "amount": amount,
                    "severity": hazard["severity"]})


def _do_party(state, effect, ctx, dice, changes):
    for field, delta in effect["party"].items():
        value = int(delta)
        if ctx.get("crit") and value > 0 and field not in _PENALTY_FIELDS:
            value *= 2
        state["party"][field] = state["party"][field] + value
        if field == "tech_debt":
            state["party"][field] = max(0, state["party"][field])
        changes.append({"kind": "party_delta", "field": field, "delta": value,
                        "value": state["party"][field]})


def _do_heal_ally(state, effect, ctx, dice, changes):
    pid = ctx.get("target_id") or ctx["actor_id"]
    char = state["characters"][pid]
    amount = _amount(effect["heal_ally"], ctx, dice)
    if ctx.get("crit"):
        amount *= 2
    before = char["stamina"]
    char["stamina"] = min(char["max_stamina"], before + amount)
    changes.append({"kind": "heal", "player_id": pid,
                    "amount": char["stamina"] - before, "stamina": char["stamina"]})


def _do_restore_focus(state, effect, ctx, dice, changes):
    spec = effect["restore_focus"]
    amount = _amount(spec["amount"], ctx, dice)
    if ctx.get("crit"):
        amount *= 2
    if spec.get("scope") == "party":
        targets = list(state["characters"])
    else:
        targets = [ctx.get("target_id") or ctx["actor_id"]]
    for pid in targets:
        char = state["characters"][pid]
        before = char["focus"]
        char["focus"] = min(char["max_focus"], before + amount)
        changes.append({"kind": "focus_restore", "player_id": pid,
                        "amount": char["focus"] - before, "focus": char["focus"]})


def _do_stress_self(state, effect, ctx, dice, changes):
    char = state["characters"][ctx["actor_id"]]
    amount = max(0, _amount(effect["stress_self"], ctx, dice))
    char["stamina"] = max(0, char["stamina"] - amount)
    changes.append({"kind": "stress", "player_id": char["player_id"], "amount": amount,
                    "stamina": char["stamina"]})


def _do_apply_condition(state, effect, ctx, dice, changes):
    spec = effect["apply_condition"]
    target = None if spec.get("scope") in ("party", "hazard") else \
        (ctx.get("target_id") or ctx["actor_id"])
    add_condition(state, spec["condition"], spec.get("scope", "party"),
                  int(spec["value"]), int(spec["rounds"]), target)
    changes.append({"kind": "condition", "name": spec["condition"],
                    "value": int(spec["value"]), "rounds": int(spec["rounds"]),
                    "scope": spec.get("scope", "party")})


def _do_reveal(state, effect, ctx, dice, changes):
    hazard = _hazard(state)
    if hazard is None:
        return
    what = effect["reveal"].get("what", "weakness")
    fields = ["weakness", "dc", "next_attack"] if what == "all" else [what]
    revealed = set(hazard.get("revealed", [])) | set(fields)
    hazard["revealed"] = sorted(revealed)
    changes.append({"kind": "reveal", "hazard_id": hazard["id"], "fields": fields})


def _do_reroll_grant(state, effect, ctx, dice, changes):
    spec = effect["reroll_grant"]
    pid = ctx.get("target_id") or ctx["actor_id"]
    add_condition(state, "reroll", "ally", int(spec.get("count", 1)), 2, pid)
    changes.append({"kind": "reroll", "player_id": pid,
                    "count": int(spec.get("count", 1))})


def _do_shield(state, effect, ctx, dice, changes):
    spec = effect["shield"]
    amount = _amount(spec["amount"], ctx, dice)
    scope = spec.get("scope", "party")
    target = None if scope == "party" else (ctx.get("target_id") or ctx["actor_id"])
    add_condition(state, "shield", scope, amount, int(spec.get("rounds", 1)), target)
    changes.append({"kind": "shield", "amount": amount, "scope": scope})


def _do_skip_hazard(state, effect, ctx, dice, changes):
    hazard = _hazard(state)
    if hazard is None:
        return
    hazard["defeated"] = True
    multiplier = float(effect["skip_hazard"].get("return_multiplier", 1.5))
    returning = dict(hazard)
    returning.update({
        "id": f"{hazard['id']}_returned",
        "name": f"{hazard['name']} (Deferred)",
        "phase_index": min(4, hazard["phase_index"] + 1),
        "max_severity": math.ceil(hazard["max_severity"] * multiplier),
        "severity": math.ceil(hazard["max_severity"] * multiplier),
        "defeated": False,
        "revealed": [],
    })
    state["hazards"].append(returning)
    changes.append({"kind": "hazard_skipped", "hazard_id": hazard["id"],
                    "returns_as": returning["id"],
                    "returns_in_phase": returning["phase_index"]})


def _do_copy_ability(state, effect, ctx, dice, changes):
    add_condition(state, "borrowed_ability", "ally", 1, 2, ctx["actor_id"])
    changes.append({"kind": "ability_borrowed", "player_id": ctx["actor_id"]})


_HANDLERS = {
    "damage_hazard": _do_damage_hazard,
    "party": _do_party,
    "heal_ally": _do_heal_ally,
    "restore_focus": _do_restore_focus,
    "stress_self": _do_stress_self,
    "apply_condition": _do_apply_condition,
    "reveal": _do_reveal,
    "reroll_grant": _do_reroll_grant,
    "shield": _do_shield,
    "skip_hazard": _do_skip_hazard,
    "copy_ability": _do_copy_ability,
}


def apply_effects(state: dict, effects: Sequence[dict], ctx: dict,
                  dice: Dice) -> list:
    """Apply each effect to `state` in order, returning the change records."""
    changes: list = []
    for effect in effects:
        _HANDLERS[_verb_of(effect)](state, effect, ctx, dice, changes)
    return changes
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/engine/test_effects.py -v`
Expected: PASS — 30 tests.

- [ ] **Step 5: Commit**

```bash
git add engine/effects.py tests/engine/test_effects.py
git commit -m "feat: declarative effect-verb interpreter"
```

---

## Task 5: Roll resolution

**Files:**
- Create: `engine/rules.py`
- Test: `tests/engine/test_rules.py`

**Interfaces:**
- Consumes: `Dice`, `Ability`, `Catalog`, `apply_effects`, `condition_total`,
  `add_condition`, `expire_conditions`.
- Produces:
  - `RuleError(Exception)` — an illegal move (wrong turn, no Focus, locked ability).
  - `stat_mod(score: int) -> int`.
  - `effective_dc(state: dict, ability: Ability, actor_id: str) -> int`.
  - `ActionResult` dataclass: `natural, stat_used, stat_mod, roll_bonus, total, dc,
    outcome, changes, rerolled`. `outcome` is `"crit" | "success" | "failure" | "fumble"`.
  - `validate_action(state, actor_id, ability) -> None`.
  - `resolve_action(state, actor_id, ability, dice, target_id=None) -> ActionResult`.
  - `hazard_attack(state, dice) -> dict` — the hazard's end-of-round response.
  - `advance_turn(state) -> bool` — returns `True` when a full round completed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_rules.py
import pytest
from engine.classes import load_catalog
from engine.dice import Dice
from engine.effects import add_condition, condition_total
from engine.rules import (ActionResult, RuleError, advance_turn, effective_dc,
                          hazard_attack, resolve_action, stat_mod, validate_action)
from tests.engine.test_effects import make_state


class FixedDice(Dice):
    """Dice whose d20 returns a scripted sequence; other rolls stay seeded."""

    def __init__(self, naturals, seed=1):
        super().__init__(seed)
        self._naturals = list(naturals)

    def d20(self):
        return self._naturals.pop(0) if self._naturals else super().d20()


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_stat_mod_matches_dnd_table():
    assert [stat_mod(n) for n in (8, 10, 12, 14, 16, 18)] == [-1, 0, 1, 2, 3, 4]


def test_effective_dc_is_the_hazard_dc_by_default(cat):
    s = make_state()
    ability = cat.abilities["refactor"]        # dc_mod +1
    assert effective_dc(s, ability, "p1") == 12 + 1


def test_tech_debt_raises_dc_one_per_ten_points(cat):
    s = make_state()
    s["party"]["tech_debt"] = 25
    ability = cat.abilities["unit_test_barrage"]   # dc_mod -1
    assert effective_dc(s, ability, "p1") == 12 - 1 + 2


def test_dc_delta_condition_lowers_the_dc(cat):
    s = make_state()
    add_condition(s, "dc_delta", "party", -3, 1)
    ability = cat.abilities["refactor"]
    assert effective_dc(s, ability, "p1") == 12 + 1 - 3


def test_success_when_total_meets_the_dc(cat):
    s = make_state()
    ability = cat.abilities["refactor"]  # RIGOR 16 -> +3, dc 13
    result = resolve_action(s, "p1", ability, FixedDice([10]))
    assert result.total == 13 and result.outcome == "success"


def test_failure_when_total_is_below_the_dc(cat):
    s = make_state()
    result = resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([5]))
    assert result.outcome == "failure"


def test_natural_twenty_is_a_crit_and_doubles_effects(cat):
    s = make_state()
    result = resolve_action(s, "p1", cat.abilities["unit_test_barrage"],
                            FixedDice([20]))
    assert result.outcome == "crit"


def test_natural_one_is_a_fumble(cat):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("descope")
    result = resolve_action(s, "p1", cat.abilities["descope"], FixedDice([1]))
    assert result.outcome == "fumble"


def test_fumble_triggers_an_immediate_hazard_attack(cat):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("descope")
    before = s["characters"]["p1"]["stamina"]
    resolve_action(s, "p1", cat.abilities["descope"], FixedDice([1]))
    assert s["characters"]["p1"]["stamina"] < before


def test_no_fumble_ability_never_fumbles(cat):
    s = make_state()
    result = resolve_action(s, "p1", cat.abilities["unit_test_barrage"],
                            FixedDice([1]))
    assert result.outcome == "failure"


def test_crit_range_condition_makes_nineteen_a_crit(cat):
    s = make_state()
    add_condition(s, "crit_range", "party", 19, 2)
    result = resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([19]))
    assert result.outcome == "crit"


def test_fixed_roll_ability_ignores_the_die(cat):
    s = make_state()
    s["characters"]["p1"]["class_id"] = "control_systems_engineer"
    s["characters"]["p1"]["unlocked"] = ["kalman_filter"]
    s["characters"]["p1"]["focus"] = 4
    result = resolve_action(s, "p1", cat.abilities["kalman_filter"], FixedDice([20]))
    assert result.natural == 11 and result.outcome != "crit"


def test_cancel_fumble_condition_converts_a_fumble_to_a_failure(cat):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("descope")
    add_condition(s, "cancel_fumble", "party", 1, 3)
    result = resolve_action(s, "p1", cat.abilities["descope"], FixedDice([1]))
    assert result.outcome == "failure"
    assert condition_total(s, "cancel_fumble", "p1") == 0


def test_reroll_condition_is_consumed_on_a_failed_roll(cat):
    s = make_state()
    add_condition(s, "reroll", "ally", 1, 2, "p1")
    result = resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([2, 18]))
    assert result.rerolled is True and result.natural == 18
    assert condition_total(s, "reroll", "p1") == 0


def test_reroll_is_not_used_when_the_first_roll_succeeds(cat):
    s = make_state()
    add_condition(s, "reroll", "ally", 1, 2, "p1")
    result = resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([18, 2]))
    assert result.rerolled is False
    assert condition_total(s, "reroll", "p1") == 1


def test_stat_alt_uses_the_higher_modifier(cat):
    s = make_state()
    s["characters"]["p1"]["class_id"] = "mechatronics_engineer"
    s["characters"]["p1"]["unlocked"] = ["sensor_fusion"]
    s["characters"]["p1"]["stats"]["CRAFT"] = 18   # beats SYSTEMS 12
    result = resolve_action(s, "p1", cat.abilities["sensor_fusion"], FixedDice([10]))
    assert result.stat_used == "CRAFT" and result.stat_mod == 4


def test_focus_is_spent_on_use(cat):
    s = make_state()
    resolve_action(s, "p1", cat.abilities["refactor"], FixedDice([15]))
    assert s["characters"]["p1"]["focus"] == 2   # 4 - 2


def test_insufficient_focus_is_rejected(cat):
    s = make_state()
    s["characters"]["p1"]["focus"] = 1
    with pytest.raises(RuleError, match="Focus"):
        validate_action(s, "p1", cat.abilities["refactor"])


def test_locked_ability_is_rejected(cat):
    s = make_state()
    with pytest.raises(RuleError, match="not unlocked"):
        validate_action(s, "p1", cat.abilities["rubber_duck"])


def test_acting_out_of_turn_is_rejected(cat):
    s = make_state()  # turn_index 0 -> p1 is active
    with pytest.raises(RuleError, match="turn"):
        validate_action(s, "p2", cat.abilities["shop_floor_fix"])


def test_burned_out_character_cannot_act(cat):
    s = make_state()
    s["characters"]["p1"]["stamina"] = 0
    with pytest.raises(RuleError, match="Burned Out"):
        validate_action(s, "p1", cat.abilities["refactor"])


def test_extra_budget_cost_is_charged_and_checked(cat):
    s = make_state()
    s["characters"]["p1"]["class_id"] = "ee_engineer"
    s["characters"]["p1"]["unlocked"] = ["board_respin"]
    resolve_action(s, "p1", cat.abilities["board_respin"], FixedDice([15]))
    assert s["party"]["budget"] == 85


def test_extra_budget_cost_is_rejected_when_unaffordable(cat):
    s = make_state()
    s["party"]["budget"] = 5
    s["characters"]["p1"]["class_id"] = "ee_engineer"
    s["characters"]["p1"]["unlocked"] = ["board_respin"]
    with pytest.raises(RuleError, match="Budget"):
        validate_action(s, "p1", cat.abilities["board_respin"])


def test_once_per_hazard_ability_is_rejected_on_reuse(cat):
    s = make_state()
    s["characters"]["p1"]["unlocked"].append("binary_search_debug")
    s["characters"]["p1"]["focus"] = 6
    resolve_action(s, "p1", cat.abilities["binary_search_debug"], FixedDice([18]))
    with pytest.raises(RuleError, match="once"):
        validate_action(s, "p1", cat.abilities["binary_search_debug"])


def test_borrowed_ability_condition_permits_a_locked_ability(cat):
    s = make_state()
    add_condition(s, "borrowed_ability", "ally", 1, 2, "p1")
    validate_action(s, "p1", cat.abilities["shop_floor_fix"])   # must not raise


def test_hazard_stress_attack_damages_a_character(cat):
    s = make_state()
    before = sum(c["stamina"] for c in s["characters"].values())
    hazard_attack(s, Dice(3))
    assert sum(c["stamina"] for c in s["characters"].values()) < before


def test_shield_absorbs_hazard_stress(cat):
    s = make_state()
    add_condition(s, "shield", "party", 99, 2)
    before = sum(c["stamina"] for c in s["characters"].values())
    hazard_attack(s, Dice(3))
    assert sum(c["stamina"] for c in s["characters"].values()) == before


def test_burn_budget_attack_drains_budget(cat):
    s = make_state()
    s["hazards"][0]["attack_type"] = "burn_budget"
    hazard_attack(s, Dice(3))
    assert s["party"]["budget"] < 100


def test_resist_burn_condition_blocks_a_burn_attack(cat):
    s = make_state()
    s["hazards"][0]["attack_type"] = "burn_schedule"
    add_condition(s, "resist_burn", "party", 1, 2)
    hazard_attack(s, Dice(3))
    assert s["party"]["schedule"] == 100


def test_no_debt_condition_blocks_a_debt_attack(cat):
    s = make_state()
    s["hazards"][0]["attack_type"] = "debt"
    add_condition(s, "no_debt", "hazard", 1, 2)
    hazard_attack(s, Dice(3))
    assert s["party"]["tech_debt"] == 0


def test_stunned_hazard_skips_its_attack(cat):
    s = make_state()
    add_condition(s, "stunned", "hazard", 1, 1)
    before = sum(c["stamina"] for c in s["characters"].values())
    hazard_attack(s, Dice(3))
    assert sum(c["stamina"] for c in s["characters"].values()) == before


def test_advance_turn_cycles_and_reports_round_completion(cat):
    s = make_state()
    assert advance_turn(s) is False      # p1 -> p2
    assert s["turn"]["turn_index"] == 1
    assert advance_turn(s) is True       # p2 -> wraps, round complete
    assert s["turn"]["round"] == 2 and s["turn"]["turn_index"] == 0


def test_advance_turn_skips_burned_out_players(cat):
    s = make_state()
    s["characters"]["p2"]["stamina"] = 0
    advance_turn(s)
    assert s["turn"]["order"][s["turn"]["turn_index"]] == "p1"


def test_focus_regenerates_one_per_turn(cat):
    s = make_state()
    s["characters"]["p2"]["focus"] = 0
    advance_turn(s)
    assert s["characters"]["p2"]["focus"] == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/engine/test_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.rules'`

- [ ] **Step 3: Implement the rules**

```python
# engine/rules.py
"""The single d20 mechanic, turn order, and the hazard's response."""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.classes import Ability
from engine.dice import Dice
from engine.effects import (active_conditions, add_condition, apply_effects,
                            condition_total)


class RuleError(Exception):
    """An illegal move. Surfaces to the player as a 400, never a crash."""


@dataclass
class ActionResult:
    natural: int
    stat_used: str
    stat_mod: int
    roll_bonus: int
    total: int
    dc: int
    outcome: str
    changes: list = field(default_factory=list)
    rerolled: bool = False


def stat_mod(score: int) -> int:
    return (score - 10) // 2


def _hazard(state):
    hid = state.get("active_hazard_id")
    for h in state["hazards"]:
        if h["id"] == hid:
            return h
    return None


def _active_player_id(state) -> str:
    turn = state["turn"]
    return turn["order"][turn["turn_index"]]


def _stat_for(state, actor_id, ability: Ability) -> tuple:
    stats = state["characters"][actor_id]["stats"]
    best, best_mod = ability.stat, stat_mod(stats[ability.stat])
    if ability.stat_alt and stat_mod(stats[ability.stat_alt]) > best_mod:
        best, best_mod = ability.stat_alt, stat_mod(stats[ability.stat_alt])
    return best, best_mod


def effective_dc(state, ability: Ability, actor_id: str) -> int:
    hazard = _hazard(state)
    base = hazard["dc"] if hazard else 12
    debt_penalty = state["party"]["tech_debt"] // 10
    delta = condition_total(state, "dc_delta", actor_id)
    return base + ability.dc_mod + debt_penalty + delta


def _once_per_key(state, ability: Ability) -> "str | None":
    if ability.once_per == "hazard":
        return f"hazard:{state.get('active_hazard_id')}"
    if ability.once_per == "phase":
        return f"phase:{state['room']['phase_index']}"
    return None


def validate_action(state, actor_id: str, ability: Ability) -> None:
    if actor_id not in state["characters"]:
        raise RuleError("You are not seated in this room.")
    char = state["characters"][actor_id]
    if _active_player_id(state) != actor_id:
        raise RuleError("It is not your turn.")
    if char["stamina"] <= 0:
        raise RuleError("You are Burned Out and cannot act.")
    borrowed = condition_total(state, "borrowed_ability", actor_id) > 0
    if ability.id not in char["unlocked"] and not borrowed:
        raise RuleError(f"{ability.name} is not unlocked for you.")
    if char["focus"] < ability.focus_cost:
        raise RuleError(
            f"{ability.name} needs {ability.focus_cost} Focus, you have {char['focus']}.")
    for resource, cost in ability.extra_cost.items():
        if state["party"][resource] < cost:
            raise RuleError(
                f"{ability.name} costs {cost} {resource.title()}, "
                f"the party has {state['party'][resource]}.")
    key = _once_per_key(state, ability)
    if key and char["used"].get(ability.id) == key:
        raise RuleError(f"{ability.name} can only be used once per {ability.once_per}.")


def resolve_action(state, actor_id: str, ability: Ability, dice: Dice,
                   target_id: "str | None" = None) -> ActionResult:
    validate_action(state, actor_id, ability)
    char = state["characters"][actor_id]
    hazard = _hazard(state)

    # Pay costs first; they are spent whether or not the roll lands.
    char["focus"] -= ability.focus_cost
    for resource, cost in ability.extra_cost.items():
        state["party"][resource] -= cost
    key = _once_per_key(state, ability)
    if key:
        char["used"][ability.id] = key
    borrowed = [c for c in active_conditions(state, "borrowed_ability", actor_id)]
    if ability.id not in char["unlocked"] and borrowed:
        state["conditions"].remove(borrowed[0])

    stat_used, mod = _stat_for(state, actor_id, ability)
    bonus = condition_total(state, "roll_bonus", actor_id)
    bonus += condition_total(state, "next_attack_bonus", actor_id)
    if hazard and "weakness" in hazard.get("revealed", []) \
            and hazard.get("weakness") == stat_used:
        bonus += 2
    dc = effective_dc(state, ability, actor_id)

    rerolled = False
    if ability.fixed_roll is not None:
        natural = ability.fixed_roll
    else:
        natural = dice.d20()
        if natural + mod + bonus < dc:
            rerolls = active_conditions(state, "reroll", actor_id)
            if rerolls:
                state["conditions"].remove(rerolls[0])
                natural = dice.d20()
                rerolled = True

    crit_values = [c["value"] for c in active_conditions(state, "crit_range", actor_id)]
    crit_floor = min(crit_values) if crit_values else 20   # stacking must not cancel
    total = natural + mod + bonus

    if ability.fixed_roll is None and natural == 1 and not ability.no_fumble:
        cancels = active_conditions(state, "cancel_fumble", actor_id)
        if cancels:
            state["conditions"].remove(cancels[0])
            outcome = "failure"
        else:
            outcome = "fumble"
    elif ability.fixed_roll is None and natural >= crit_floor and not ability.no_crit:
        outcome = "crit"
    else:
        outcome = "success" if total >= dc else "failure"

    ctx = {"actor_id": actor_id, "target_id": target_id,
           "mods": {k: stat_mod(v) for k, v in char["stats"].items()},
           "crit": outcome == "crit", "ability": ability}
    effects = ability.on_success if outcome in ("success", "crit") else ability.on_fail
    changes = apply_effects(state, list(effects), ctx, dice)

    # Consume single-use attack bonuses now that the roll is spent.
    for c in list(active_conditions(state, "next_attack_bonus", actor_id)):
        state["conditions"].remove(c)

    if outcome == "fumble":
        changes.append(hazard_attack(state, dice, forced_target=actor_id))

    state["last_ability_id"] = ability.id
    return ActionResult(natural=natural, stat_used=stat_used, stat_mod=mod,
                        roll_bonus=bonus, total=total, dc=dc, outcome=outcome,
                        changes=changes, rerolled=rerolled)


def hazard_attack(state, dice: Dice, forced_target: "str | None" = None) -> dict:
    """The hazard's response. Attack type is fixed per hazard, never random."""
    hazard = _hazard(state)
    if hazard is None or hazard.get("defeated"):
        return {"kind": "hazard_attack", "blocked": "no_hazard"}
    if condition_total(state, "stunned") > 0:
        for c in list(active_conditions(state, "stunned")):
            state["conditions"].remove(c)
        return {"kind": "hazard_attack", "blocked": "stunned"}

    phase = state["room"]["phase_index"]
    attack = hazard["attack_type"]

    if attack == "stress":
        living = [p for p, c in state["characters"].items() if c["stamina"] > 0]
        target = forced_target if forced_target in living else (
            dice.choice(sorted(living)) if living else None)
        if target is None:
            return {"kind": "hazard_attack", "blocked": "party_down"}
        amount = dice.roll(f"1d6+{phase + 1}", {})
        shields = active_conditions(state, "shield", target)
        absorbed = 0
        for shield in shields:
            take = min(shield["value"], amount - absorbed)
            shield["value"] -= take
            absorbed += take
            if shield["value"] <= 0:
                state["conditions"].remove(shield)
            if absorbed >= amount:
                break
        net = amount - absorbed
        char = state["characters"][target]
        char["stamina"] = max(0, char["stamina"] - net)
        return {"kind": "hazard_attack", "attack": "stress", "player_id": target,
                "amount": net, "absorbed": absorbed, "stamina": char["stamina"]}

    if attack in ("burn_budget", "burn_schedule"):
        if condition_total(state, "resist_burn") > 0:
            return {"kind": "hazard_attack", "blocked": "resist_burn"}
        field_name = "budget" if attack == "burn_budget" else "schedule"
        amount = dice.roll(f"1d6+{phase + 2}", {})
        state["party"][field_name] -= amount
        return {"kind": "hazard_attack", "attack": attack, "field": field_name,
                "amount": amount, "value": state["party"][field_name]}

    if attack == "debt":
        if condition_total(state, "no_debt") > 0:
            return {"kind": "hazard_attack", "blocked": "no_debt"}
        amount = dice.roll("1d4", {}) + phase
        state["party"]["tech_debt"] += amount
        return {"kind": "hazard_attack", "attack": "debt", "amount": amount,
                "value": state["party"]["tech_debt"]}

    raise RuleError(f"unknown hazard attack type: {attack!r}")


def advance_turn(state) -> bool:
    """Move to the next living player. Returns True when a round completed."""
    turn = state["turn"]
    order = turn["order"]
    completed = False
    for _ in range(len(order)):
        turn["turn_index"] += 1
        if turn["turn_index"] >= len(order):
            turn["turn_index"] = 0
            turn["round"] += 1
            completed = True
        candidate = order[turn["turn_index"]]
        if state["characters"][candidate]["stamina"] > 0:
            break
    char = state["characters"][order[turn["turn_index"]]]
    char["focus"] = min(char["max_focus"], char["focus"] + 1)
    return completed
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/engine/test_rules.py -v`
Expected: PASS — 33 tests.

- [ ] **Step 5: Commit**

```bash
git add engine/rules.py tests/engine/test_rules.py
git commit -m "feat: d20 resolution, crit/fumble handling, turn order, hazard attacks"
```

---

## Task 6: Character creation and level up

**Files:**
- Create: `engine/character.py`
- Test: `tests/engine/test_character.py`

**Interfaces:**
- Consumes: `Dice`, `Catalog`, `CharacterClass`, `STATS`, `stat_mod`.
- Produces:
  - `roll_stats(cls: CharacterClass, dice: Dice) -> dict[str, int]`.
  - `max_stamina(stats: dict, level: int) -> int` — `8 + GRIT_mod + 2 * level`.
  - `max_focus(stats: dict) -> int` — `4 + max(RIGOR_mod, SYSTEMS_mod)`.
  - `new_character(player_id, name, cls, catalog, dice) -> dict`.
  - `level_up(char, catalog, stat_choice, phase_index) -> dict` — returns a summary
    `{"level", "stat_raised", "max_stamina", "new_abilities": [ability_id]}`.
  - `CharacterError(Exception)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_character.py
import pytest
from engine.character import (CharacterError, level_up, max_focus, max_stamina,
                              new_character, roll_stats)
from engine.classes import STATS, load_catalog
from engine.dice import Dice


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_roll_stats_produces_all_six(cat):
    stats = roll_stats(cat.classes["mechanical_engineer"], Dice(5))
    assert set(stats) == set(STATS)


def test_rolled_stats_stay_in_the_eight_to_sixteen_band(cat):
    for seed in range(50):
        stats = roll_stats(cat.classes["computer_scientist"], Dice(seed))
        assert all(8 <= v <= 16 for v in stats.values()), stats


def test_primary_stat_is_the_highest_rolled(cat):
    cls = cat.classes["product_manager"]     # COMMS / SYSTEMS
    for seed in range(30):
        stats = roll_stats(cls, Dice(seed))
        assert stats[cls.primary] == max(stats.values())


def test_secondary_is_at_least_as_high_as_the_other_four(cat):
    cls = cat.classes["product_manager"]
    for seed in range(30):
        stats = roll_stats(cls, Dice(seed))
        others = [v for k, v in stats.items()
                  if k not in (cls.primary, cls.secondary)]
        assert stats[cls.secondary] >= max(others)


def test_roll_stats_is_deterministic_for_a_seed(cat):
    cls = cat.classes["system_engineer"]
    assert roll_stats(cls, Dice(77)) == roll_stats(cls, Dice(77))


def test_max_stamina_follows_the_spec_formula():
    stats = {"RIGOR": 10, "INTUITION": 10, "CRAFT": 10,
             "SYSTEMS": 10, "COMMS": 10, "GRIT": 14}
    assert max_stamina(stats, 1) == 8 + 2 + 2


def test_max_focus_takes_the_better_of_rigor_and_systems():
    stats = {"RIGOR": 16, "INTUITION": 10, "CRAFT": 10,
             "SYSTEMS": 12, "COMMS": 10, "GRIT": 10}
    assert max_focus(stats) == 4 + 3


def test_new_character_starts_at_full_stamina_and_focus(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    assert char["stamina"] == char["max_stamina"]
    assert char["focus"] == char["max_focus"]
    assert char["level"] == 1


def test_new_character_knows_exactly_two_abilities(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    assert len(char["unlocked"]) == 2


def test_new_character_records_identity(cat):
    char = new_character("p7", "Ada", cat.classes["ee_engineer"], cat, Dice(9))
    assert char["player_id"] == "p7" and char["name"] == "Ada"
    assert char["class_id"] == "ee_engineer" and char["used"] == {}


def test_level_up_raises_the_chosen_stat(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    before = char["stats"]["GRIT"]
    level_up(char, cat, "GRIT", 1)
    assert char["stats"]["GRIT"] == before + 1


def test_level_up_increases_level_and_max_stamina(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    before = char["max_stamina"]
    level_up(char, cat, "RIGOR", 1)
    assert char["level"] == 2 and char["max_stamina"] == before + 2


def test_level_up_unlocks_newly_available_abilities(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    summary = level_up(char, cat, "RIGOR", 2)
    assert "refactor" in summary["new_abilities"]
    assert "refactor" in char["unlocked"]


def test_level_up_restores_focus_to_full(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    char["focus"] = 0
    level_up(char, cat, "RIGOR", 1)
    assert char["focus"] == char["max_focus"]


def test_level_up_clears_once_per_phase_usage(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    char["used"]["binary_search_debug"] = "phase:0"
    level_up(char, cat, "RIGOR", 1)
    assert char["used"] == {}


def test_level_up_rejects_an_unknown_stat(cat):
    char = new_character("p1", "Ada", cat.classes["computer_scientist"], cat, Dice(9))
    with pytest.raises(CharacterError, match="MAGIC"):
        level_up(char, cat, "MAGIC", 1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/engine/test_character.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.character'`

- [ ] **Step 3: Implement**

```python
# engine/character.py
"""Character creation and level up."""
from __future__ import annotations

from engine.classes import STATS, Catalog, CharacterClass
from engine.dice import Dice
from engine.rules import stat_mod


class CharacterError(Exception):
    """Raised on an invalid character operation."""


def _four_d6_drop_lowest(dice: Dice) -> int:
    rolls = sorted(dice.randint(1, 6) for _ in range(4))
    return sum(rolls[1:])


def roll_stats(cls: CharacterClass, dice: Dice) -> dict:
    """Roll 4d6-drop-lowest six times, seat the best two in the class stats."""
    values = sorted((_four_d6_drop_lowest(dice) for _ in range(6)), reverse=True)
    values = [max(8, min(16, v)) for v in values]
    rest = [s for s in STATS if s not in (cls.primary, cls.secondary)]
    dice.shuffle(rest)
    stats = {cls.primary: values[0], cls.secondary: values[1]}
    for name, value in zip(rest, values[2:]):
        stats[name] = value
    return {s: stats[s] for s in STATS}


def max_stamina(stats: dict, level: int) -> int:
    return 8 + stat_mod(stats["GRIT"]) + 2 * level


def max_focus(stats: dict) -> int:
    return 4 + max(stat_mod(stats["RIGOR"]), stat_mod(stats["SYSTEMS"]))


def new_character(player_id: str, name: str, cls: CharacterClass,
                  catalog: Catalog, dice: Dice) -> dict:
    stats = roll_stats(cls, dice)
    stamina, focus = max_stamina(stats, 1), max_focus(stats)
    return {
        "player_id": player_id,
        "name": name,
        "class_id": cls.id,
        "stats": stats,
        "level": 1,
        "stamina": stamina,
        "max_stamina": stamina,
        "focus": focus,
        "max_focus": focus,
        "unlocked": [a.id for a in catalog.unlocked_for(cls.id, 0)],
        "used": {},
    }


def level_up(char: dict, catalog: Catalog, stat_choice: str,
             phase_index: int) -> dict:
    if stat_choice not in STATS:
        raise CharacterError(f"unknown stat {stat_choice!r}")
    char["stats"][stat_choice] += 1
    char["level"] += 1
    char["max_stamina"] = max_stamina(char["stats"], char["level"])
    char["stamina"] = min(char["max_stamina"], max(1, char["stamina"]) + 2)
    char["max_focus"] = max_focus(char["stats"])
    char["focus"] = char["max_focus"]
    char["used"] = {}

    available = {a.id for a in catalog.unlocked_for(char["class_id"], phase_index)}
    new_abilities = sorted(available - set(char["unlocked"]))
    char["unlocked"].extend(new_abilities)
    return {
        "level": char["level"],
        "stat_raised": stat_choice,
        "max_stamina": char["max_stamina"],
        "new_abilities": new_abilities,
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/engine/test_character.py -v`
Expected: PASS — 16 tests.

- [ ] **Step 5: Commit**

```bash
git add engine/character.py tests/engine/test_character.py
git commit -m "feat: character creation, stat rolling, and level up"
```

---

## Task 7: Phases, campaign construction, and end conditions

**Files:**
- Create: `engine/phases.py`, `data/hazard_templates.json`, `data/archetypes.json`
- Test: `tests/engine/test_phases.py`

**Interfaces:**
- Consumes: `Dice`, `Catalog`, `level_up`, `expire_conditions`.
- Produces:
  - `PHASES: tuple[tuple[str, str], ...]` — `(id, display_name)` pairs, five entries.
  - `load_hazard_templates(data_dir="data") -> dict`.
  - `load_archetypes(data_dir="data") -> list[dict]`.
  - `build_campaign(dice, templates) -> list[dict]` — every hazard for all five phases.
  - `next_hazard_id(state) -> str | None` — first undefeated hazard in the current phase.
  - `phase_cleared(state) -> bool`.
  - `advance_phase(state, catalog, stat_choices) -> dict` — returns a summary.
  - `check_end_conditions(state) -> str | None` — `"win"`, `"lose_budget"`,
    `"lose_schedule"`, `"lose_burnout"`, or `None`.
  - `PHASE_BUDGET_BONUS = 10`, `PHASE_SCHEDULE_BONUS = 10`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_phases.py
import pytest
from engine.classes import load_catalog
from engine.dice import Dice
from engine.phases import (PHASES, advance_phase, build_campaign,
                           check_end_conditions, load_archetypes,
                           load_hazard_templates, next_hazard_id, phase_cleared)
from tests.engine.test_effects import make_state


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


@pytest.fixture(scope="module")
def templates():
    return load_hazard_templates()


def test_five_phases_in_the_specified_order():
    assert [p[0] for p in PHASES] == [
        "requirements", "design", "prototype", "integration", "qualification"]


def test_archetypes_file_lists_at_least_the_eight_named_systems():
    archetypes = load_archetypes()
    ids = {a["id"] for a in archetypes}
    assert {"car", "aircraft", "weapon_platform", "spacecraft"} <= ids
    assert len(archetypes) >= 8


def test_every_phase_has_normal_templates_and_a_boss(templates):
    for phase_id, _ in PHASES:
        assert len(templates[phase_id]["normal"]) >= 3
        assert templates[phase_id]["boss"]["is_boss"] is True


def test_all_template_attack_types_are_valid(templates):
    valid = {"stress", "burn_budget", "burn_schedule", "debt"}
    for phase_id, _ in PHASES:
        entries = templates[phase_id]["normal"] + [templates[phase_id]["boss"]]
        assert all(e["attack_type"] in valid for e in entries)


def test_build_campaign_covers_every_phase(templates):
    hazards = build_campaign(Dice(3), templates)
    for index in range(5):
        assert any(h["phase_index"] == index for h in hazards)


def test_each_phase_has_two_or_three_normals_plus_one_boss(templates):
    hazards = build_campaign(Dice(3), templates)
    for index in range(5):
        in_phase = [h for h in hazards if h["phase_index"] == index]
        bosses = [h for h in in_phase if h["is_boss"]]
        assert len(bosses) == 1
        assert 2 <= len(in_phase) - 1 <= 3


def test_boss_is_always_last_in_its_phase(templates):
    hazards = build_campaign(Dice(3), templates)
    for index in range(5):
        in_phase = [h for h in hazards if h["phase_index"] == index]
        assert in_phase[-1]["is_boss"] is True


def test_severity_and_dc_scale_with_phase(templates):
    hazards = build_campaign(Dice(3), templates)
    first = [h for h in hazards if h["phase_index"] == 0 and not h["is_boss"]][0]
    last = [h for h in hazards if h["phase_index"] == 4 and not h["is_boss"]][0]
    assert last["max_severity"] > first["max_severity"]
    assert last["dc"] > first["dc"]


def test_hazard_ids_are_unique(templates):
    hazards = build_campaign(Dice(3), templates)
    assert len({h["id"] for h in hazards}) == len(hazards)


def test_build_campaign_is_deterministic_for_a_seed(templates):
    assert build_campaign(Dice(11), templates) == build_campaign(Dice(11), templates)


def test_next_hazard_id_returns_the_first_undefeated_in_phase(templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    first = next_hazard_id(s)
    for h in s["hazards"]:
        if h["id"] == first:
            h["defeated"] = True
    assert next_hazard_id(s) != first


def test_phase_cleared_only_when_all_phase_hazards_are_down(templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    assert phase_cleared(s) is False
    for h in s["hazards"]:
        if h["phase_index"] == 0:
            h["defeated"] = True
    assert phase_cleared(s) is True


def test_advance_phase_moves_forward_and_replenishes(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    s["party"]["budget"] = 50
    summary = advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert s["room"]["phase_index"] == 1
    assert s["party"]["budget"] == 60
    assert summary["phase"] == "design"


def test_advance_phase_levels_every_character(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert all(c["level"] == 2 for c in s["characters"].values())


def test_advance_phase_revives_burned_out_characters(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    s["characters"]["p2"]["stamina"] = 0
    advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert s["characters"]["p2"]["stamina"] > 0


def test_advance_phase_clears_lingering_conditions(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    s["conditions"] = [{"name": "roll_bonus", "scope": "party", "value": 3,
                        "rounds": 9, "target_id": None}]
    advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert s["conditions"] == []


def test_clearing_the_final_phase_wins(cat, templates):
    s = make_state()
    s["hazards"] = build_campaign(Dice(3), templates)
    s["room"]["phase_index"] = 4
    for h in s["hazards"]:
        h["defeated"] = True
    advance_phase(s, cat, {"p1": "RIGOR", "p2": "GRIT"})
    assert s["room"]["status"] == "won"
    assert check_end_conditions(s) == "win"


def test_zero_budget_loses():
    s = make_state()
    s["party"]["budget"] = 0
    assert check_end_conditions(s) == "lose_budget"


def test_negative_schedule_loses():
    s = make_state()
    s["party"]["schedule"] = -2
    assert check_end_conditions(s) == "lose_schedule"


def test_total_burnout_loses():
    s = make_state()
    for c in s["characters"].values():
        c["stamina"] = 0
    assert check_end_conditions(s) == "lose_burnout"


def test_partial_burnout_does_not_lose():
    s = make_state()
    s["characters"]["p2"]["stamina"] = 0
    assert check_end_conditions(s) is None


def test_healthy_game_has_no_end_condition():
    assert check_end_conditions(make_state()) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/engine/test_phases.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.phases'`

- [ ] **Step 3: Write `data/archetypes.json`**

```json
[
  {"id": "car", "name": "Passenger Vehicle", "hint": "a mid-size electric passenger car"},
  {"id": "aircraft", "name": "Aircraft", "hint": "a four-seat hybrid-electric trainer aircraft"},
  {"id": "weapon_platform", "name": "Weapon Platform", "hint": "a vehicle-mounted remote weapon station"},
  {"id": "spacecraft", "name": "Spacecraft", "hint": "a small satellite bus for low Earth orbit"},
  {"id": "surgical_robot", "name": "Surgical Robot", "hint": "a seven-axis laparoscopic surgical manipulator"},
  {"id": "robot_arm", "name": "Industrial Robot Arm", "hint": "a six-axis industrial pick-and-place arm"},
  {"id": "submarine", "name": "Submersible", "hint": "a crewed shallow-water research submersible"},
  {"id": "wind_turbine", "name": "Wind Turbine", "hint": "an offshore direct-drive wind turbine nacelle"}
]
```

- [ ] **Step 4: Write `data/hazard_templates.json`**

These are the deterministic fallbacks used when the LLM is absent, and the structural
skeleton the LLM rewrites during genesis (Task 17). Three normals plus one boss per phase:

```json
{
  "requirements": {
    "normal": [
      {"name": "Ambiguous Customer Requirement", "description": "Clause 4.2 says 'shall be robust'. Nobody will say robust to what.", "attack_type": "debt", "weakness": "COMMS"},
      {"name": "Untestable Acceptance Criterion", "description": "It is measurable in principle and in no laboratory that exists.", "attack_type": "burn_schedule", "weakness": "RIGOR"},
      {"name": "Silent Stakeholder", "description": "The one group that has not reviewed the spec is the one that will reject it.", "attack_type": "stress", "weakness": "COMMS"},
      {"name": "Requirement Creep", "description": "Two new shalls appeared overnight and nobody owns them.", "attack_type": "burn_budget", "weakness": "SYSTEMS"}
    ],
    "boss": {"name": "System Requirements Review", "description": "Forty people, ninety slides, and one unanswered question about the thermal case.", "attack_type": "stress", "weakness": "SYSTEMS", "is_boss": true}
  },
  "design": {
    "normal": [
      {"name": "Thermal Budget Overrun", "description": "The heat has to go somewhere and every somewhere is already occupied.", "attack_type": "stress", "weakness": "CRAFT"},
      {"name": "Interface Mismatch", "description": "Two subsystems agree on the connector and on nothing else.", "attack_type": "debt", "weakness": "SYSTEMS"},
      {"name": "Mass Growth", "description": "Every allocation grew by eight percent. The total grew by thirty.", "attack_type": "burn_budget", "weakness": "RIGOR"},
      {"name": "Single-Source Component", "description": "One supplier, one lead time, one very bad day.", "attack_type": "burn_schedule", "weakness": "COMMS"}
    ],
    "boss": {"name": "Preliminary Design Review", "description": "The architecture is defensible. Defending it takes eleven hours.", "attack_type": "burn_schedule", "weakness": "RIGOR", "is_boss": true}
  },
  "prototype": {
    "normal": [
      {"name": "Machining Tolerance Escape", "description": "The bore is 0.3 mm over. The drawing said 0.05. Both parties have opinions.", "attack_type": "burn_budget", "weakness": "CRAFT"},
      {"name": "Firmware Bring-Up Stall", "description": "The board powers on, enumerates nothing, and blames the host.", "attack_type": "burn_schedule", "weakness": "INTUITION"},
      {"name": "Harness Mis-Pinning", "description": "Pin 7 and pin 9 have been swapped since the first prototype.", "attack_type": "stress", "weakness": "CRAFT"},
      {"name": "Fixture Not Ready", "description": "The part exists. Nothing can hold it.", "attack_type": "burn_schedule", "weakness": "GRIT"}
    ],
    "boss": {"name": "First Article Inspection", "description": "Every dimension, every finish, every one of them measured in front of the customer.", "attack_type": "stress", "weakness": "CRAFT", "is_boss": true}
  },
  "integration": {
    "normal": [
      {"name": "Harmonic Coupling", "description": "At 43 Hz the whole structure agrees to move as one. It should not.", "attack_type": "stress", "weakness": "SYSTEMS"},
      {"name": "Intermittent Bus Fault", "description": "It fails once every four hours and never while instrumented.", "attack_type": "burn_schedule", "weakness": "INTUITION"},
      {"name": "Control Loop Instability", "description": "The gain that worked on the bench oscillates on the vehicle.", "attack_type": "stress", "weakness": "RIGOR"},
      {"name": "EMC Emissions Failure", "description": "Twelve dB over the limit at 140 MHz, from a cable nobody documented.", "attack_type": "burn_budget", "weakness": "CRAFT"}
    ],
    "boss": {"name": "Test Readiness Review", "description": "You must prove you are ready to be tested. That is two arguments, not one.", "attack_type": "debt", "weakness": "COMMS", "is_boss": true}
  },
  "qualification": {
    "normal": [
      {"name": "Vibration Test Failure", "description": "Bracket fractured at 11 minutes into a 60 minute profile.", "attack_type": "burn_budget", "weakness": "CRAFT"},
      {"name": "Thermal Cycling Drift", "description": "The calibration holds at 20 C and wanders everywhere else.", "attack_type": "stress", "weakness": "RIGOR"},
      {"name": "Documentation Gap", "description": "The unit passes. The evidence that it passes does not exist yet.", "attack_type": "burn_schedule", "weakness": "COMMS"},
      {"name": "Late Regulatory Change", "description": "The standard was revised in March. You have been building to the 2019 edition.", "attack_type": "debt", "weakness": "SYSTEMS"}
    ],
    "boss": {"name": "Qualification Test Campaign", "description": "Everything, at once, to the limits, with the customer watching.", "attack_type": "stress", "weakness": "GRIT", "is_boss": true}
  }
}
```

- [ ] **Step 5: Implement `engine/phases.py`**

```python
# engine/phases.py
"""Phase progression, campaign construction, and win/lose conditions."""
from __future__ import annotations

import json
from pathlib import Path

from engine.character import level_up
from engine.classes import Catalog
from engine.dice import Dice

PHASES: tuple = (
    ("requirements", "Requirements"),
    ("design", "Design"),
    ("prototype", "Prototype"),
    ("integration", "Integration"),
    ("qualification", "Qualification"),
)

PHASE_BUDGET_BONUS = 10
PHASE_SCHEDULE_BONUS = 10

_NORMAL_SEVERITY_BASE, _NORMAL_SEVERITY_STEP = 20, 8
_BOSS_SEVERITY_BASE, _BOSS_SEVERITY_STEP = 35, 10
_NORMAL_DC_BASE, _BOSS_DC_BASE = 11, 13


def load_hazard_templates(data_dir: str = "data") -> dict:
    return json.loads((Path(data_dir) / "hazard_templates.json").read_text())


def load_archetypes(data_dir: str = "data") -> list:
    return json.loads((Path(data_dir) / "archetypes.json").read_text())


def _hazard_from_template(template: dict, phase_index: int, ordinal: int,
                          is_boss: bool) -> dict:
    if is_boss:
        max_severity = _BOSS_SEVERITY_BASE + _BOSS_SEVERITY_STEP * phase_index
        dc = _BOSS_DC_BASE + phase_index
    else:
        max_severity = _NORMAL_SEVERITY_BASE + _NORMAL_SEVERITY_STEP * phase_index
        dc = _NORMAL_DC_BASE + phase_index
    return {
        "id": f"h{phase_index}_{ordinal}",
        "phase_index": phase_index,
        "ordinal": ordinal,
        "name": template["name"],
        "description": template["description"],
        "severity": max_severity,
        "max_severity": max_severity,
        "dc": dc,
        "attack_type": template["attack_type"],
        "weakness": template["weakness"],
        "revealed": [],
        "defeated": False,
        "is_boss": is_boss,
    }


def build_campaign(dice: Dice, templates: dict) -> list:
    """Build every hazard for all five phases. Boss is always last in its phase."""
    hazards: list = []
    for phase_index, (phase_id, _) in enumerate(PHASES):
        pool = list(templates[phase_id]["normal"])
        dice.shuffle(pool)
        count = dice.randint(2, 3)
        for ordinal, template in enumerate(pool[:count]):
            hazards.append(_hazard_from_template(template, phase_index, ordinal, False))
        hazards.append(_hazard_from_template(
            templates[phase_id]["boss"], phase_index, count, True))
    return hazards


def next_hazard_id(state) -> "str | None":
    phase_index = state["room"]["phase_index"]
    for hazard in state["hazards"]:
        if hazard["phase_index"] == phase_index and not hazard["defeated"]:
            return hazard["id"]
    return None


def phase_cleared(state) -> bool:
    return next_hazard_id(state) is None


def check_end_conditions(state) -> "str | None":
    if state["room"].get("status") == "won":
        return "win"
    if state["party"]["budget"] <= 0:
        return "lose_budget"
    if state["party"]["schedule"] <= 0:
        return "lose_schedule"
    if state["characters"] and all(
            c["stamina"] <= 0 for c in state["characters"].values()):
        return "lose_burnout"
    return None


def advance_phase(state, catalog: Catalog, stat_choices: dict) -> dict:
    """Clear the phase: level everyone, replenish, revive, and move on."""
    current = state["room"]["phase_index"]
    if current >= len(PHASES) - 1:
        state["room"]["status"] = "won"
        return {"phase": PHASES[current][0], "won": True, "levelled": {}}

    new_index = current + 1
    state["room"]["phase_index"] = new_index
    state["party"]["budget"] += PHASE_BUDGET_BONUS
    state["party"]["schedule"] += PHASE_SCHEDULE_BONUS
    state["conditions"] = []

    levelled = {}
    for player_id, char in state["characters"].items():
        if char["stamina"] <= 0:
            char["stamina"] = 1          # revived, but only just
        levelled[player_id] = level_up(
            char, catalog, stat_choices.get(player_id, "GRIT"), new_index)

    state["active_hazard_id"] = next_hazard_id(state)
    state["turn"]["round"] = 1
    state["turn"]["turn_index"] = 0
    return {"phase": PHASES[new_index][0], "won": False, "levelled": levelled}
```

- [ ] **Step 6: Run to verify they pass**

Run: `pytest tests/engine/ -v`
Expected: PASS — 22 phase tests, and every earlier engine test still green.

- [ ] **Step 7: Commit**

```bash
git add engine/phases.py data/hazard_templates.json data/archetypes.json tests/engine/test_phases.py
git commit -m "feat: five-phase progression, campaign construction, win/lose conditions"
```

---

## Task 8: Per-room database

**Files:**
- Create: `storage/schema.sql`, `storage/room_db.py`
- Test: `tests/storage/test_room_db.py`

**Interfaces:**
- Consumes: nothing from `engine/` (storage must not import it).
- Produces:
  - `RoomDB` with classmethods `create(root, room_id, name, archetype, rng_seed) -> RoomDB`
    and `open(root, room_id) -> RoomDB`; `exists(root, room_id) -> bool`;
    `list_room_ids(root) -> list[str]`.
  - Instance methods: `connect() -> sqlite3.Connection` (one per thread),
    `close()`, `load_state() -> dict`, `save_state(state: dict) -> None`,
    `add_player(player_id, name, token, class_id)`,
    `player_by_token(token) -> dict | None`, `players() -> list[dict]`,
    `append_event(kind, actor, payload) -> int`,
    `events_since(seq, limit=500) -> list[dict]`, `latest_seq() -> int`,
    `create_narration(event_seq)`, `update_narration(event_seq, status, text, source)`,
    `narration(event_seq) -> dict | None`.
  - `RoomNotFound(Exception)`.

The state dict is exactly the shape Task 4 established, so `load_state(save_state(s))`
is an identity round trip.

- [ ] **Step 1: Write the failing tests**

```python
# tests/storage/test_room_db.py
import json
import pytest
from storage.room_db import RoomDB, RoomNotFound
from tests.engine.test_effects import make_state


@pytest.fixture
def room(tmp_path):
    db = RoomDB.create(str(tmp_path), "abc123", "Kestrel", "aircraft", 4242)
    yield db
    db.close()


def seat(room, state):
    """Characters FK to players, so a forged state needs its players seated first."""
    for player_id, char in state["characters"].items():
        room.add_player(player_id, char["name"], f"tok-{player_id}", char["class_id"])


def test_create_makes_the_file(tmp_path, room):
    assert (tmp_path / "abc123" / "game.db").exists()


def test_exists_reports_presence(tmp_path, room):
    assert RoomDB.exists(str(tmp_path), "abc123") is True
    assert RoomDB.exists(str(tmp_path), "nope") is False


def test_open_missing_room_raises(tmp_path):
    with pytest.raises(RoomNotFound):
        RoomDB.open(str(tmp_path), "ghost")


def test_wal_mode_is_enabled(room):
    mode = room.connect().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_new_room_starts_with_spec_default_resources(room):
    state = room.load_state()
    assert state["party"] == {"budget": 100, "schedule": 100, "tech_debt": 0}


def test_new_room_records_its_identity(room):
    state = room.load_state()
    assert state["room"]["id"] == "abc123"
    assert state["room"]["name"] == "Kestrel"
    assert state["room"]["archetype"] == "aircraft"
    assert state["room"]["rng_seed"] == 4242
    assert state["room"]["phase_index"] == 0
    assert state["room"]["status"] == "lobby"


def test_state_round_trips_unchanged(room):
    original = make_state()
    original["room"].update({"id": "abc123", "name": "Kestrel",
                             "archetype": "aircraft", "rng_seed": 4242,
                             "premise": "A trainer aircraft."})
    seat(room, original)
    room.save_state(original)
    assert room.load_state() == original


def test_state_survives_closing_and_reopening(tmp_path, room):
    s = make_state()
    s["room"].update({"id": "abc123", "name": "Kestrel", "archetype": "aircraft",
                      "rng_seed": 4242, "premise": "p"})
    s["party"]["tech_debt"] = 17
    seat(room, s)
    room.save_state(s)
    room.close()
    reopened = RoomDB.open(str(tmp_path), "abc123")
    assert reopened.load_state()["party"]["tech_debt"] == 17
    reopened.close()


def test_save_state_replaces_rather_than_duplicating_hazards(room):
    s = make_state()
    s["room"].update({"id": "abc123", "name": "K", "archetype": "aircraft",
                      "rng_seed": 1, "premise": "p"})
    seat(room, s)
    room.save_state(s)
    room.save_state(s)
    assert len(room.load_state()["hazards"]) == len(s["hazards"])


def test_add_player_and_lookup_by_token(room):
    room.add_player("p1", "Ada", "tok-1", "computer_scientist")
    found = room.player_by_token("tok-1")
    assert found["player_id"] == "p1" and found["class_id"] == "computer_scientist"


def test_player_by_unknown_token_is_none(room):
    assert room.player_by_token("nope") is None


def test_players_lists_everyone_in_join_order(room):
    room.add_player("p1", "Ada", "t1", "computer_scientist")
    room.add_player("p2", "Ben", "t2", "mechanical_technician")
    assert [p["player_id"] for p in room.players()] == ["p1", "p2"]


def test_append_event_returns_increasing_sequence_numbers(room):
    a = room.append_event("action", "p1", {"x": 1})
    b = room.append_event("action", "p1", {"x": 2})
    assert b == a + 1


def test_events_since_excludes_earlier_events(room):
    first = room.append_event("a", None, {})
    room.append_event("b", None, {})
    room.append_event("c", None, {})
    kinds = [e["kind"] for e in room.events_since(first)]
    assert kinds == ["b", "c"]


def test_events_since_zero_replays_everything(room):
    room.append_event("a", None, {})
    room.append_event("b", None, {})
    assert len(room.events_since(0)) == 2


def test_event_payload_round_trips_as_json(room):
    seq = room.append_event("roll", "p1", {"natural": 17, "tags": ["crit"]})
    event = room.events_since(seq - 1)[0]
    assert event["payload"] == {"natural": 17, "tags": ["crit"]}


def test_latest_seq_tracks_the_last_append(room):
    assert room.latest_seq() == 0
    seq = room.append_event("a", None, {})
    assert room.latest_seq() == seq


def test_narration_lifecycle_from_pending_to_done(room):
    seq = room.append_event("action", "p1", {})
    room.create_narration(seq)
    assert room.narration(seq)["status"] == "pending"
    room.update_narration(seq, "done", "The bracket holds.", "llm")
    done = room.narration(seq)
    assert done["status"] == "done"
    assert done["text"] == "The bracket holds."
    assert done["source"] == "llm"


def test_narration_for_unknown_event_is_none(room):
    assert room.narration(999) is None


def test_list_room_ids_finds_created_rooms(tmp_path, room):
    RoomDB.create(str(tmp_path), "def456", "Osprey", "car", 1).close()
    assert sorted(RoomDB.list_room_ids(str(tmp_path))) == ["abc123", "def456"]


def test_list_room_ids_ignores_directories_without_a_database(tmp_path, room):
    (tmp_path / "junk").mkdir()
    assert "junk" not in RoomDB.list_room_ids(str(tmp_path))
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/storage/test_room_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storage.room_db'`

- [ ] **Step 3: Write the schema**

```sql
-- storage/schema.sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS room (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    premise        TEXT NOT NULL DEFAULT '',
    archetype      TEXT NOT NULL,
    phase_index    INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'lobby',
    rng_seed       INTEGER NOT NULL,
    created_at     REAL NOT NULL,
    last_active    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS party (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    budget      INTEGER NOT NULL DEFAULT 100,
    schedule    INTEGER NOT NULL DEFAULT 100,
    tech_debt   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS players (
    player_id    TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    token        TEXT NOT NULL UNIQUE,
    class_id     TEXT NOT NULL,
    joined_at    REAL NOT NULL,
    last_seen    REAL NOT NULL,
    seat         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS characters (
    player_id    TEXT PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    class_id     TEXT NOT NULL,
    stats        TEXT NOT NULL,
    level        INTEGER NOT NULL,
    stamina      INTEGER NOT NULL,
    max_stamina  INTEGER NOT NULL,
    focus        INTEGER NOT NULL,
    max_focus    INTEGER NOT NULL,
    unlocked     TEXT NOT NULL,
    used         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hazards (
    id            TEXT PRIMARY KEY,
    phase_index   INTEGER NOT NULL,
    ordinal       INTEGER NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    severity      INTEGER NOT NULL,
    max_severity  INTEGER NOT NULL,
    dc            INTEGER NOT NULL,
    attack_type   TEXT NOT NULL,
    weakness      TEXT NOT NULL,
    revealed      TEXT NOT NULL DEFAULT '[]',
    defeated      INTEGER NOT NULL DEFAULT 0,
    is_boss       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turn_state (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    round             INTEGER NOT NULL DEFAULT 1,
    turn_index        INTEGER NOT NULL DEFAULT 0,
    turn_order        TEXT NOT NULL DEFAULT '[]',
    active_hazard_id  TEXT,
    conditions        TEXT NOT NULL DEFAULT '[]',
    last_ability_id   TEXT
);

CREATE TABLE IF NOT EXISTS events (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    kind     TEXT NOT NULL,
    actor    TEXT,
    payload  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS narrations (
    event_seq INTEGER PRIMARY KEY REFERENCES events(seq) ON DELETE CASCADE,
    status    TEXT NOT NULL DEFAULT 'pending',
    text      TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_hazards_phase ON hazards(phase_index, ordinal);
```

- [ ] **Step 4: Implement `storage/room_db.py`**

```python
# storage/room_db.py
"""One SQLite database per room. The source of truth for a single game."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class RoomNotFound(Exception):
    """Raised when a room directory or database is absent."""


class RoomDB:
    """Connections are per-thread; Flask's threaded server needs that."""

    def __init__(self, db_path: Path, room_id: str) -> None:
        self.path = Path(db_path)
        self.room_id = room_id
        self._local = threading.local()

    # --- lifecycle ---------------------------------------------------------

    @staticmethod
    def _db_path(root: str, room_id: str) -> Path:
        return Path(root) / room_id / "game.db"

    @classmethod
    def exists(cls, root: str, room_id: str) -> bool:
        return cls._db_path(root, room_id).exists()

    @classmethod
    def list_room_ids(cls, root: str) -> list:
        base = Path(root)
        if not base.exists():
            return []
        return [p.name for p in base.iterdir()
                if p.is_dir() and (p / "game.db").exists()]

    @classmethod
    def create(cls, root: str, room_id: str, name: str, archetype: str,
               rng_seed: int) -> "RoomDB":
        path = cls._db_path(root, room_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = cls(path, room_id)
        conn = db.connect()
        conn.executescript(_SCHEMA)
        now = time.time()
        conn.execute(
            "INSERT OR REPLACE INTO room "
            "(id, name, premise, archetype, phase_index, status, rng_seed,"
            " created_at, last_active) VALUES (?,?,?,?,0,'lobby',?,?,?)",
            (room_id, name, "", archetype, rng_seed, now, now))
        conn.execute("INSERT OR IGNORE INTO party (id) VALUES (1)")
        conn.execute("INSERT OR IGNORE INTO turn_state (id) VALUES (1)")
        conn.commit()
        return db

    @classmethod
    def open(cls, root: str, room_id: str) -> "RoomDB":
        path = cls._db_path(root, room_id)
        if not path.exists():
            raise RoomNotFound(room_id)
        return cls(path, room_id)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # --- state -------------------------------------------------------------

    def load_state(self) -> dict:
        conn = self.connect()
        room = dict(conn.execute("SELECT * FROM room WHERE id = ?",
                                 (self.room_id,)).fetchone())
        party = dict(conn.execute("SELECT * FROM party WHERE id = 1").fetchone())
        turn = dict(conn.execute("SELECT * FROM turn_state WHERE id = 1").fetchone())

        characters = {}
        for row in conn.execute("SELECT * FROM characters"):
            row = dict(row)
            characters[row["player_id"]] = {
                "player_id": row["player_id"], "name": row["name"],
                "class_id": row["class_id"], "stats": json.loads(row["stats"]),
                "level": row["level"], "stamina": row["stamina"],
                "max_stamina": row["max_stamina"], "focus": row["focus"],
                "max_focus": row["max_focus"],
                "unlocked": json.loads(row["unlocked"]),
                "used": json.loads(row["used"]),
            }

        hazards = []
        for row in conn.execute(
                "SELECT * FROM hazards ORDER BY phase_index, ordinal"):
            row = dict(row)
            row["revealed"] = json.loads(row["revealed"])
            row["defeated"] = bool(row["defeated"])
            row["is_boss"] = bool(row["is_boss"])
            hazards.append(row)

        state = {
            "room": {"id": room["id"], "name": room["name"],
                     "premise": room["premise"], "archetype": room["archetype"],
                     "phase_index": room["phase_index"], "status": room["status"],
                     "rng_seed": room["rng_seed"]},
            "party": {"budget": party["budget"], "schedule": party["schedule"],
                      "tech_debt": party["tech_debt"]},
            "characters": characters,
            "hazards": hazards,
            "active_hazard_id": turn["active_hazard_id"],
            "turn": {"round": turn["round"], "turn_index": turn["turn_index"],
                     "order": json.loads(turn["turn_order"])},
            "conditions": json.loads(turn["conditions"]),
        }
        if turn["last_ability_id"]:
            state["last_ability_id"] = turn["last_ability_id"]
        return state

    def save_state(self, state: dict) -> None:
        conn = self.connect()
        room, party = state["room"], state["party"]
        with conn:
            conn.execute(
                "UPDATE room SET name=?, premise=?, archetype=?, phase_index=?,"
                " status=?, rng_seed=?, last_active=? WHERE id=?",
                (room["name"], room.get("premise", ""), room["archetype"],
                 room["phase_index"], room["status"], room["rng_seed"],
                 time.time(), self.room_id))
            conn.execute(
                "UPDATE party SET budget=?, schedule=?, tech_debt=? WHERE id=1",
                (party["budget"], party["schedule"], party["tech_debt"]))
            conn.execute(
                "UPDATE turn_state SET round=?, turn_index=?, turn_order=?,"
                " active_hazard_id=?, conditions=?, last_ability_id=? WHERE id=1",
                (state["turn"]["round"], state["turn"]["turn_index"],
                 json.dumps(state["turn"]["order"]), state.get("active_hazard_id"),
                 json.dumps(state.get("conditions", [])),
                 state.get("last_ability_id")))

            conn.execute("DELETE FROM characters")
            for char in state["characters"].values():
                conn.execute(
                    "INSERT INTO characters (player_id, name, class_id, stats, level,"
                    " stamina, max_stamina, focus, max_focus, unlocked, used)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (char["player_id"], char["name"], char["class_id"],
                     json.dumps(char["stats"]), char["level"], char["stamina"],
                     char["max_stamina"], char["focus"], char["max_focus"],
                     json.dumps(char["unlocked"]), json.dumps(char["used"])))

            conn.execute("DELETE FROM hazards")
            for hazard in state["hazards"]:
                conn.execute(
                    "INSERT INTO hazards (id, phase_index, ordinal, name, description,"
                    " severity, max_severity, dc, attack_type, weakness, revealed,"
                    " defeated, is_boss) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (hazard["id"], hazard["phase_index"], hazard["ordinal"],
                     hazard["name"], hazard["description"], hazard["severity"],
                     hazard["max_severity"], hazard["dc"], hazard["attack_type"],
                     hazard["weakness"], json.dumps(hazard["revealed"]),
                     int(hazard["defeated"]), int(hazard["is_boss"])))

    # --- players -----------------------------------------------------------

    def add_player(self, player_id: str, name: str, token: str,
                   class_id: str) -> None:
        conn = self.connect()
        seat = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        now = time.time()
        with conn:
            conn.execute(
                "INSERT INTO players (player_id, display_name, token, class_id,"
                " joined_at, last_seen, seat) VALUES (?,?,?,?,?,?,?)",
                (player_id, name, token, class_id, now, now, seat))

    def player_by_token(self, token: str) -> "dict | None":
        row = self.connect().execute(
            "SELECT * FROM players WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None

    def players(self) -> list:
        return [dict(r) for r in self.connect().execute(
            "SELECT * FROM players ORDER BY seat")]

    # --- events and narration ---------------------------------------------

    def append_event(self, kind: str, actor: "str | None", payload: dict) -> int:
        conn = self.connect()
        with conn:
            cur = conn.execute(
                "INSERT INTO events (ts, kind, actor, payload) VALUES (?,?,?,?)",
                (time.time(), kind, actor, json.dumps(payload)))
        return cur.lastrowid

    def events_since(self, seq: int, limit: int = 500) -> list:
        rows = self.connect().execute(
            "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
            (seq, limit))
        out = []
        for row in rows:
            row = dict(row)
            row["payload"] = json.loads(row["payload"])
            out.append(row)
        return out

    def latest_seq(self) -> int:
        row = self.connect().execute("SELECT MAX(seq) FROM events").fetchone()
        return row[0] or 0

    def create_narration(self, event_seq: int) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO narrations (event_seq, status, text, source)"
                " VALUES (?, 'pending', '', '')", (event_seq,))

    def update_narration(self, event_seq: int, status: str, text: str,
                         source: str) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT INTO narrations (event_seq, status, text, source)"
                " VALUES (?,?,?,?) ON CONFLICT(event_seq) DO UPDATE SET"
                " status=excluded.status, text=excluded.text, source=excluded.source",
                (event_seq, status, text, source))

    def narration(self, event_seq: int) -> "dict | None":
        row = self.connect().execute(
            "SELECT * FROM narrations WHERE event_seq = ?", (event_seq,)).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/storage/test_room_db.py -v`
Expected: PASS — 20 tests.

- [ ] **Step 6: Commit**

```bash
git add storage/schema.sql storage/room_db.py tests/storage/test_room_db.py
git commit -m "feat: per-room SQLite database with append-only event log"
```

---

## Task 9: Lobby index

**Files:**
- Create: `storage/index_db.py`
- Test: `tests/storage/test_index_db.py`

**Interfaces:**
- Consumes: `RoomDB` (Task 8).
- Produces:
  - `IndexDB(root: str)` with `list_rooms() -> list[dict]`,
    `upsert(room_id, name, archetype, phase_index, status, player_count)`,
    `remove(room_id)`, `rebuild() -> int` (returns rooms indexed), `close()`.

`index.db` is a rebuildable cache, never the source of truth. `rebuild()` must be able
to reconstruct it entirely from `rooms/*/game.db`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/storage/test_index_db.py
import pytest
from storage.index_db import IndexDB
from storage.room_db import RoomDB


@pytest.fixture
def index(tmp_path):
    db = IndexDB(str(tmp_path))
    yield db
    db.close()


def test_empty_index_lists_nothing(index):
    assert index.list_rooms() == []


def test_upsert_then_list(index):
    index.upsert("abc", "Kestrel", "aircraft", 0, "lobby", 2)
    rooms = index.list_rooms()
    assert len(rooms) == 1
    assert rooms[0]["room_id"] == "abc" and rooms[0]["player_count"] == 2


def test_upsert_is_idempotent_and_updates_in_place(index):
    index.upsert("abc", "Kestrel", "aircraft", 0, "lobby", 2)
    index.upsert("abc", "Kestrel", "aircraft", 3, "active", 4)
    rooms = index.list_rooms()
    assert len(rooms) == 1
    assert rooms[0]["phase_index"] == 3 and rooms[0]["status"] == "active"


def test_list_is_ordered_most_recently_active_first(index):
    index.upsert("old", "Old", "car", 0, "lobby", 1)
    index.upsert("new", "New", "car", 0, "lobby", 1)
    assert [r["room_id"] for r in index.list_rooms()] == ["new", "old"]


def test_remove_deletes_the_row(index):
    index.upsert("abc", "Kestrel", "aircraft", 0, "lobby", 1)
    index.remove("abc")
    assert index.list_rooms() == []


def test_rebuild_reconstructs_from_room_databases(tmp_path, index):
    RoomDB.create(str(tmp_path), "r1", "Kestrel", "aircraft", 1).close()
    RoomDB.create(str(tmp_path), "r2", "Osprey", "car", 2).close()
    assert index.rebuild() == 2
    assert {r["room_id"] for r in index.list_rooms()} == {"r1", "r2"}


def test_rebuild_drops_rows_for_rooms_that_no_longer_exist(tmp_path, index):
    index.upsert("ghost", "Ghost", "car", 0, "lobby", 1)
    assert index.rebuild() == 0
    assert index.list_rooms() == []


def test_rebuild_captures_player_counts(tmp_path, index):
    room = RoomDB.create(str(tmp_path), "r1", "Kestrel", "aircraft", 1)
    room.add_player("p1", "Ada", "t1", "computer_scientist")
    room.add_player("p2", "Ben", "t2", "mechanical_technician")
    room.close()
    index.rebuild()
    assert index.list_rooms()[0]["player_count"] == 2


def test_index_file_lives_beside_the_rooms(tmp_path, index):
    index.upsert("abc", "K", "car", 0, "lobby", 1)
    assert (tmp_path / "index.db").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/storage/test_index_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storage.index_db'`

- [ ] **Step 3: Implement**

```python
# storage/index_db.py
"""Lobby registry. A rebuildable cache over rooms/*/game.db, never authoritative."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from storage.room_db import RoomDB

_DDL = """
CREATE TABLE IF NOT EXISTS rooms (
    room_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    archetype    TEXT NOT NULL,
    phase_index  INTEGER NOT NULL,
    status       TEXT NOT NULL,
    player_count INTEGER NOT NULL,
    last_active  REAL NOT NULL
);
"""


class IndexDB:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "index.db"
        self._local = threading.local()
        self.connect().executescript(_DDL)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def list_rooms(self) -> list:
        return [dict(r) for r in self.connect().execute(
            "SELECT * FROM rooms ORDER BY last_active DESC")]

    def upsert(self, room_id: str, name: str, archetype: str, phase_index: int,
               status: str, player_count: int) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                "INSERT INTO rooms (room_id, name, archetype, phase_index, status,"
                " player_count, last_active) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(room_id) DO UPDATE SET name=excluded.name,"
                " archetype=excluded.archetype, phase_index=excluded.phase_index,"
                " status=excluded.status, player_count=excluded.player_count,"
                " last_active=excluded.last_active",
                (room_id, name, archetype, phase_index, status, player_count,
                 time.time()))

    def remove(self, room_id: str) -> None:
        conn = self.connect()
        with conn:
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))

    def rebuild(self) -> int:
        """Reconstruct the whole index by scanning the room databases."""
        conn = self.connect()
        with conn:
            conn.execute("DELETE FROM rooms")
        count = 0
        for room_id in RoomDB.list_room_ids(str(self.root)):
            room = RoomDB.open(str(self.root), room_id)
            try:
                state = room.load_state()
                self.upsert(room_id, state["room"]["name"],
                            state["room"]["archetype"],
                            state["room"]["phase_index"], state["room"]["status"],
                            len(room.players()))
                count += 1
            finally:
                room.close()
        return count
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/storage/ -v`
Expected: PASS — 9 index tests plus Task 8's 20.

- [ ] **Step 5: Commit**

```bash
git add storage/index_db.py tests/storage/test_index_db.py
git commit -m "feat: rebuildable lobby index over room databases"
```

---

## Task 10: Orchestration service

**Files:**
- Create: `service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: everything from `engine/` and `storage/`.
- Produces:
  - `GameService(root, catalog, templates, archetypes, queue=None)`.
  - `ServiceError(Exception)`; re-exports `RuleError` for callers.
  - Methods: `create_room(name, archetype) -> str`, `list_rooms() -> list[dict]`,
    `join_room(room_id, display_name, class_id) -> dict` (returns
    `{"player_id", "token", "character"}`), `start_game(room_id) -> None`,
    `snapshot(room_id) -> dict`, `act(room_id, player_id, ability_id, target_id=None) -> dict`,
    `end_turn(room_id, player_id) -> dict`,
    `set_level_choice(room_id, player_id, stat) -> None`,
    `events_since(room_id, seq) -> list[dict]`,
    `player_by_token(room_id, token) -> dict | None`,
    `narration(room_id, event_seq) -> dict | None`.

**Two refinements over the spec, both deliberate:**

1. **The campaign is built deterministically at room creation**, from
   `hazard_templates.json`, so a room is playable the instant it exists. Genesis (Task 17)
   later *rewrites* hazard names and descriptions in place. The spec implied players wait
   for genesis; they do not, and the scheduling win is the same.
2. **Level-up stat choice** is recorded per player via `set_level_choice`; if a player
   has not chosen when the phase clears, their class's primary stat is raised. This keeps
   spec §2.6's "stat of choice" without blocking the phase transition on nine prompts.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_service.py
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


def test_a_room_survives_a_fresh_service_instance(tmp_path):
    catalog, templates, archetypes = (
        load_catalog(), load_hazard_templates(), load_archetypes())
    first = GameService(str(tmp_path), catalog, templates, archetypes)
    room_id, a, _ = seat_two(first)
    first.act(room_id, a["player_id"], "unit_test_barrage")
    focus = first.snapshot(room_id)["characters"][a["player_id"]]["focus"]

    second = GameService(str(tmp_path), catalog, templates, archetypes)
    assert second.snapshot(room_id)["characters"][a["player_id"]]["focus"] == focus
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'service'`

- [ ] **Step 3: Implement**

```python
# service.py
"""Orchestration: per-room locks, and the seam between pure engine and storage."""
from __future__ import annotations

import secrets
import threading

from engine.character import new_character
from engine.classes import STATS, Catalog
from engine.dice import Dice
from engine.effects import expire_conditions
from engine.phases import (PHASES, advance_phase, build_campaign,
                           check_end_conditions, next_hazard_id, phase_cleared)
from engine.rules import RuleError, advance_turn, hazard_attack, resolve_action
from storage.index_db import IndexDB
from storage.room_db import RoomDB, RoomNotFound

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no look-alike characters


class ServiceError(Exception):
    """A request that is wrong about the world: unknown room, taken class, game over."""


class GameService:
    def __init__(self, root: str, catalog: Catalog, templates: dict,
                 archetypes: list, queue=None) -> None:
        self.root = root
        self.catalog = catalog
        self.templates = templates
        self.archetypes = {a["id"]: a for a in archetypes}
        self.queue = queue
        self.index = IndexDB(root)
        self._locks: dict = {}
        self._locks_guard = threading.Lock()
        self._rooms: dict = {}
        self._level_choices: dict = {}

    # --- plumbing ----------------------------------------------------------

    def _lock(self, room_id: str) -> threading.Lock:
        with self._locks_guard:
            if room_id not in self._locks:
                self._locks[room_id] = threading.Lock()
            return self._locks[room_id]

    def _room(self, room_id: str) -> RoomDB:
        if room_id not in self._rooms:
            try:
                self._rooms[room_id] = RoomDB.open(self.root, room_id)
            except RoomNotFound as exc:
                raise ServiceError(f"no such room: {room_id}") from exc
        return self._rooms[room_id]

    def _dice(self, state: dict) -> Dice:
        """Advance the room seed each call so replays differ but stay reproducible."""
        seed = state["room"]["rng_seed"]
        state["room"]["rng_seed"] = (seed * 6364136223846793005 + 1) % (2 ** 63)
        return Dice(seed)

    def _reindex(self, room_id: str, state: dict) -> None:
        self.index.upsert(room_id, state["room"]["name"], state["room"]["archetype"],
                          state["room"]["phase_index"], state["room"]["status"],
                          len(state["characters"]))

    # --- lobby -------------------------------------------------------------

    def create_room(self, name: str, archetype: str) -> str:
        if archetype not in self.archetypes:
            raise ServiceError(f"unknown archetype: {archetype}")
        while True:
            room_id = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            if not RoomDB.exists(self.root, room_id):
                break
        seed = secrets.randbelow(2 ** 31)
        room = RoomDB.create(self.root, room_id, name, archetype, seed)
        self._rooms[room_id] = room
        state = room.load_state()
        state["hazards"] = build_campaign(Dice(seed), self.templates)
        state["active_hazard_id"] = next_hazard_id(state)
        room.save_state(state)
        room.append_event("room_created", None,
                          {"name": name, "archetype": archetype})
        self._reindex(room_id, state)
        return room_id

    def list_rooms(self) -> list:
        return self.index.list_rooms()

    def join_room(self, room_id: str, display_name: str, class_id: str) -> dict:
        if class_id not in self.catalog.classes:
            raise ServiceError(f"unknown class: {class_id}")
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            if any(c["class_id"] == class_id for c in state["characters"].values()):
                raise ServiceError(f"{class_id} is already taken in this room")
            player_id = "p" + secrets.token_hex(4)
            token = secrets.token_urlsafe(24)
            char = new_character(player_id, display_name,
                                 self.catalog.classes[class_id], self.catalog,
                                 self._dice(state))
            room.add_player(player_id, display_name, token, class_id)
            state["characters"][player_id] = char
            state["turn"]["order"].append(player_id)
            room.save_state(state)
            room.append_event("player_joined", player_id,
                              {"name": display_name, "class_id": class_id})
            self._reindex(room_id, state)
            return {"player_id": player_id, "token": token, "character": char}

    def player_by_token(self, room_id: str, token: str) -> "dict | None":
        return self._room(room_id).player_by_token(token)

    def start_game(self, room_id: str) -> None:
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            if not state["characters"]:
                raise ServiceError("a room needs at least one player to start")
            state["room"]["status"] = "active"
            state["active_hazard_id"] = next_hazard_id(state)
            room.save_state(state)
            room.append_event("game_started", None,
                              {"players": state["turn"]["order"]})
            self._reindex(room_id, state)

    # --- reads -------------------------------------------------------------

    def snapshot(self, room_id: str) -> dict:
        return self._room(room_id).load_state()

    def events_since(self, room_id: str, seq: int) -> list:
        return self._room(room_id).events_since(seq)

    def narration(self, room_id: str, event_seq: int) -> "dict | None":
        return self._room(room_id).narration(event_seq)

    def set_level_choice(self, room_id: str, player_id: str, stat: str) -> None:
        if stat not in STATS:
            raise ServiceError(f"unknown stat: {stat}")
        self._level_choices.setdefault(room_id, {})[player_id] = stat

    # --- the turn ----------------------------------------------------------

    def _stat_choices(self, room_id: str, state: dict) -> dict:
        chosen = self._level_choices.get(room_id, {})
        out = {}
        for player_id, char in state["characters"].items():
            out[player_id] = chosen.get(
                player_id, self.catalog.classes[char["class_id"]].primary)
        return out

    def _after_turn(self, room_id: str, room: RoomDB, state: dict,
                    events: list) -> None:
        """Hazard progression, phase transitions, and end conditions."""
        if state["active_hazard_id"]:
            active = [h for h in state["hazards"]
                      if h["id"] == state["active_hazard_id"]]
            if active and active[0]["defeated"]:
                events.append(room.append_event(
                    "hazard_defeated", None, {"hazard_id": active[0]["id"],
                                              "name": active[0]["name"]}))
                state["active_hazard_id"] = next_hazard_id(state)

        if phase_cleared(state):
            summary = advance_phase(state, self.catalog,
                                    self._stat_choices(room_id, state))
            self._level_choices.pop(room_id, None)
            events.append(room.append_event("phase_advanced", None, summary))

        ending = check_end_conditions(state)
        if ending == "win":
            state["room"]["status"] = "won"
        elif ending:
            state["room"]["status"] = ending.replace("lose_", "lost_")
        if ending:
            events.append(room.append_event("game_over", None,
                                            {"result": ending}))

    def _require_running(self, state: dict) -> None:
        if state["room"]["status"] not in ("active", "lobby"):
            raise ServiceError("this game is over")

    def act(self, room_id: str, player_id: str, ability_id: str,
            target_id: "str | None" = None) -> dict:
        ability = self.catalog.abilities.get(ability_id)
        if ability is None:
            raise ServiceError(f"unknown ability: {ability_id}")
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            self._require_running(state)
            dice = self._dice(state)
            result = resolve_action(state, player_id, ability, dice, target_id)

            action_seq = room.append_event("action", player_id, {
                "ability_id": ability.id, "ability_name": ability.name,
                "natural": result.natural, "stat": result.stat_used,
                "stat_mod": result.stat_mod, "roll_bonus": result.roll_bonus,
                "total": result.total, "dc": result.dc, "outcome": result.outcome,
                "rerolled": result.rerolled, "changes": result.changes,
            })
            room.create_narration(action_seq)
            event_seqs = [action_seq]

            round_done = advance_turn(state)
            if round_done:
                attack = hazard_attack(state, dice)
                expire_conditions(state)
                state["party"]["schedule"] -= 1
                event_seqs.append(room.append_event("hazard_attack", None, attack))

            self._after_turn(room_id, room, state, event_seqs)
            room.save_state(state)              # commit BEFORE queueing narration
            self._reindex(room_id, state)

        self._enqueue_narration(room_id, player_id, action_seq, state, ability, result)
        return {"event_seq": action_seq, "outcome": result.outcome,
                "natural": result.natural, "total": result.total, "dc": result.dc,
                "changes": result.changes,
                "events": self._room(room_id).events_since(action_seq - 1)}

    def end_turn(self, room_id: str, player_id: str) -> dict:
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            self._require_running(state)
            if state["turn"]["order"][state["turn"]["turn_index"]] != player_id:
                raise RuleError("It is not your turn.")
            start_seq = room.latest_seq()
            room.append_event("passed", player_id, {})
            round_done = advance_turn(state)
            if round_done:
                attack = hazard_attack(state, self._dice(state))
                expire_conditions(state)
                state["party"]["schedule"] -= 1
                room.append_event("hazard_attack", None, attack)
            self._after_turn(room_id, room, state, [])
            room.save_state(state)
            self._reindex(room_id, state)
            return {"events": room.events_since(start_seq)}

    # --- narration hand-off -------------------------------------------------

    def _enqueue_narration(self, room_id, player_id, event_seq, state, ability,
                           result) -> None:
        if self.queue is None:
            return
        hazard = next((h for h in state["hazards"]
                       if h["id"] == state.get("active_hazard_id")), None)
        char = state["characters"][player_id]
        self.queue.submit({
            "kind": "turn", "priority": 0, "room_id": room_id,
            "event_seq": event_seq,
            "actor_name": char["name"],
            "actor_class": self.catalog.classes[char["class_id"]].name,
            "premise": state["room"].get("premise", ""),
            "phase": PHASES[state["room"]["phase_index"]][1],
            "hazard": hazard, "ability_name": ability.name,
            "outcome": result.outcome, "natural": result.natural,
            "total": result.total, "dc": result.dc, "changes": result.changes,
        })

    # --- test seams ---------------------------------------------------------

    def _set_party(self, room_id: str, **fields) -> None:
        """Force party resource values. Tests only; never called by the app."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            state["party"].update(fields)
            room.save_state(state)

    def _force_defeat_active_hazard(self, room_id: str) -> None:
        """Mark the active hazard defeated and progress. Tests only."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            for hazard in state["hazards"]:
                if hazard["id"] == state["active_hazard_id"]:
                    hazard["severity"] = 0
                    hazard["defeated"] = True
            self._after_turn(room_id, room, state, [])
            room.save_state(state)
            self._reindex(room_id, state)

    def _force_clear_phase(self, room_id: str) -> None:
        """Defeat every hazard in the current phase. Tests only."""
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            for hazard in state["hazards"]:
                if hazard["phase_index"] == state["room"]["phase_index"]:
                    hazard["severity"] = 0
                    hazard["defeated"] = True
            self._after_turn(room_id, room, state, [])
            room.save_state(state)
            self._reindex(room_id, state)
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_service.py -v`
Expected: PASS — 29 tests.

- [ ] **Step 5: Commit**

```bash
git add service.py tests/test_service.py
git commit -m "feat: game service with per-room locking and commit-before-narrate ordering"
```

---

## Task 11: Flask app factory and lobby API

**Files:**
- Create: `app.py`, `tests/api/conftest.py`
- Test: `tests/api/test_lobby_api.py`

**Interfaces:**
- Consumes: `GameService`, `load_catalog`, `load_hazard_templates`, `load_archetypes`.
- Produces:
  - `create_app(config: dict | None = None) -> Flask`. Config keys: `ROOMS_ROOT`
    (default `"rooms"`), `DATA_DIR` (default `"data"`), `SECRET_KEY`,
    `NARRATION` (a queue object or `None`), `TESTING`.
  - `app.service` — the `GameService` instance, so tests and the worker reach it.
  - Routes: `GET /api/archetypes`, `GET /api/classes`, `GET /api/rooms`,
    `POST /api/rooms`, `POST /api/rooms/<id>/join`, `POST /api/rooms/<id>/start`,
    `GET /api/rooms/<id>/state`, `POST /api/rooms/<id>/level-choice`.
  - `session_key(room_id) -> str` — the Flask session key holding a player's identity.
  - Error contract: `RuleError` and `ServiceError` both render as
    `400 {"error": "<message>"}`. Nothing else leaks a traceback to a player.

- [ ] **Step 1: Write the shared fixture**

```python
# tests/api/conftest.py
import pytest
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "test-secret",
                       "TESTING": True, "NARRATION": None})


@pytest.fixture
def client(app):
    return app.test_client()


def make_room(client, name="Kestrel", archetype="aircraft"):
    return client.post("/api/rooms", json={"name": name,
                                           "archetype": archetype}).get_json()["room_id"]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/api/test_lobby_api.py
from tests.api.conftest import make_room


def test_archetypes_endpoint_lists_options(client):
    data = client.get("/api/archetypes").get_json()
    assert any(a["id"] == "aircraft" for a in data["archetypes"])


def test_classes_endpoint_lists_all_nine_with_abilities(client):
    data = client.get("/api/classes").get_json()
    assert len(data["classes"]) == 9
    for entry in data["classes"]:
        assert len(entry["abilities"]) == 4
        assert entry["primary"] and entry["role"]


def test_create_room_returns_an_id(client):
    response = client.post("/api/rooms", json={"name": "Kestrel",
                                               "archetype": "aircraft"})
    assert response.status_code == 201
    assert response.get_json()["room_id"]


def test_create_room_with_a_bad_archetype_is_a_400(client):
    response = client.post("/api/rooms", json={"name": "X", "archetype": "nope"})
    assert response.status_code == 400
    assert "archetype" in response.get_json()["error"]


def test_create_room_without_a_name_is_a_400(client):
    response = client.post("/api/rooms", json={"archetype": "aircraft"})
    assert response.status_code == 400


def test_rooms_listing_includes_a_created_room(client):
    room_id = make_room(client)
    rooms = client.get("/api/rooms").get_json()["rooms"]
    assert room_id in {r["room_id"] for r in rooms}


def test_join_seats_a_character(client):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/join",
                           json={"display_name": "Ada",
                                 "class_id": "computer_scientist"})
    assert response.status_code == 200
    assert response.get_json()["character"]["class_id"] == "computer_scientist"


def test_the_session_survives_across_requests(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    # A second, independent request still knows who you are: the cookie holds.
    assert client.get(f"/api/rooms/{room_id}/state").get_json()["you"] is not None


def test_state_identifies_the_joined_player(client):
    room_id = make_room(client)
    joined = client.post(f"/api/rooms/{room_id}/join",
                         json={"display_name": "Ada",
                               "class_id": "computer_scientist"}).get_json()
    state = client.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["you"]["player_id"] == joined["player_id"]


def test_state_for_an_anonymous_visitor_has_no_you(client):
    room_id = make_room(client)
    assert client.get(f"/api/rooms/{room_id}/state").get_json()["you"] is None


def test_joining_a_taken_class_is_a_400(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    other = client.application.test_client()
    response = other.post(f"/api/rooms/{room_id}/join",
                          json={"display_name": "Ben",
                                "class_id": "computer_scientist"})
    assert response.status_code == 400
    assert "taken" in response.get_json()["error"]


def test_joining_an_unknown_room_is_a_400(client):
    response = client.post("/api/rooms/zzzzzz/join",
                           json={"display_name": "Ada",
                                 "class_id": "computer_scientist"})
    assert response.status_code == 400


def test_start_activates_the_room(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    assert client.post(f"/api/rooms/{room_id}/start").status_code == 200
    assert client.get(f"/api/rooms/{room_id}/state").get_json()["room"]["status"] == "active"


def test_start_with_an_empty_room_is_a_400(client):
    room_id = make_room(client)
    assert client.post(f"/api/rooms/{room_id}/start").status_code == 400


def test_state_hides_unrevealed_hazard_fields(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    client.post(f"/api/rooms/{room_id}/start")
    hazard = client.get(f"/api/rooms/{room_id}/state").get_json()["hazard"]
    assert hazard["weakness"] is None and hazard["dc"] is None
    assert hazard["name"] and hazard["severity"]


def test_level_choice_accepts_a_valid_stat(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    response = client.post(f"/api/rooms/{room_id}/level-choice", json={"stat": "GRIT"})
    assert response.status_code == 200


def test_level_choice_rejects_an_unknown_stat(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    response = client.post(f"/api/rooms/{room_id}/level-choice", json={"stat": "LUCK"})
    assert response.status_code == 400


def test_level_choice_without_a_seat_is_a_403(client):
    room_id = make_room(client)
    assert client.post(f"/api/rooms/{room_id}/level-choice",
                       json={"stat": "GRIT"}).status_code == 403
```

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/api/test_lobby_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 4: Implement `app.py`**

```python
# app.py
"""Flask application: HTML pages, JSON API, and the SSE event stream."""
from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session

from engine.classes import STATS, load_catalog
from engine.phases import PHASES, load_archetypes, load_hazard_templates
from engine.rules import RuleError
from service import GameService, ServiceError


def session_key(room_id: str) -> str:
    return f"room:{room_id}"


def _secret_key() -> str:
    path = Path("instance") / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(48))
    return path.read_text().strip()


def _public_hazard(state: dict) -> "dict | None":
    """Hide a hazard's DC, weakness, and next attack until they are revealed."""
    hazard = next((h for h in state["hazards"]
                   if h["id"] == state.get("active_hazard_id")), None)
    if hazard is None:
        return None
    revealed = set(hazard.get("revealed", []))
    return {
        "id": hazard["id"], "name": hazard["name"],
        "description": hazard["description"], "severity": hazard["severity"],
        "max_severity": hazard["max_severity"], "is_boss": hazard["is_boss"],
        "attack_type": hazard["attack_type"] if "next_attack" in revealed else None,
        "weakness": hazard["weakness"] if "weakness" in revealed else None,
        "dc": hazard["dc"] if "dc" in revealed else None,
    }


def create_app(config: "dict | None" = None) -> Flask:
    app = Flask(__name__)
    app.config.update(ROOMS_ROOT="rooms", DATA_DIR="data", NARRATION=None)
    app.config.update(config or {})
    app.secret_key = app.config.get("SECRET_KEY") or _secret_key()

    data_dir = app.config["DATA_DIR"]
    catalog = load_catalog(data_dir)
    app.catalog = catalog
    app.service = GameService(app.config["ROOMS_ROOT"], catalog,
                              load_hazard_templates(data_dir),
                              load_archetypes(data_dir),
                              queue=app.config.get("NARRATION"))
    app.archetypes = load_archetypes(data_dir)

    # --- error contract ----------------------------------------------------

    @app.errorhandler(ServiceError)
    @app.errorhandler(RuleError)
    def _player_error(exc):
        return jsonify({"error": str(exc)}), 400

    # --- helpers -----------------------------------------------------------

    def seat(room_id):
        return session.get(session_key(room_id))

    def require_seat(room_id):
        found = seat(room_id)
        if not found:
            return None
        return found

    # --- reference data ----------------------------------------------------

    @app.get("/api/archetypes")
    def api_archetypes():
        return jsonify({"archetypes": app.archetypes})

    @app.get("/api/classes")
    def api_classes():
        out = []
        for cls in catalog.classes.values():
            out.append({
                "id": cls.id, "name": cls.name, "primary": cls.primary,
                "secondary": cls.secondary, "role": cls.role, "blurb": cls.blurb,
                "abilities": [
                    {"id": a.id, "name": a.name, "focus_cost": a.focus_cost,
                     "stat": a.stat, "unlock_phase": a.unlock_phase,
                     "flavor": a.flavor}
                    for a in sorted(catalog.abilities_for(cls.id),
                                    key=lambda a: (a.unlock_phase, a.name))],
            })
        return jsonify({"classes": out, "stats": list(STATS),
                        "phases": [{"id": p, "name": n} for p, n in PHASES]})

    # --- lobby -------------------------------------------------------------

    @app.get("/api/rooms")
    def api_rooms():
        return jsonify({"rooms": app.service.list_rooms()})

    @app.post("/api/rooms")
    def api_create_room():
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            raise ServiceError("a room needs a name")
        room_id = app.service.create_room(name, body.get("archetype", ""))
        return jsonify({"room_id": room_id}), 201

    @app.post("/api/rooms/<room_id>/join")
    def api_join(room_id):
        body = request.get_json(silent=True) or {}
        name = (body.get("display_name") or "").strip()
        if not name:
            raise ServiceError("you need a display name")
        joined = app.service.join_room(room_id, name, body.get("class_id", ""))
        session[session_key(room_id)] = {"player_id": joined["player_id"],
                                         "token": joined["token"]}
        session.permanent = True
        return jsonify(joined)

    @app.post("/api/rooms/<room_id>/start")
    def api_start(room_id):
        app.service.start_game(room_id)
        return jsonify({"ok": True})

    @app.post("/api/rooms/<room_id>/level-choice")
    def api_level_choice(room_id):
        found = require_seat(room_id)
        if not found:
            return jsonify({"error": "you are not seated in this room"}), 403
        body = request.get_json(silent=True) or {}
        app.service.set_level_choice(room_id, found["player_id"],
                                     body.get("stat", ""))
        return jsonify({"ok": True})

    # --- state -------------------------------------------------------------

    @app.get("/api/rooms/<room_id>/state")
    def api_state(room_id):
        state = app.service.snapshot(room_id)
        found = seat(room_id)
        you = None
        if found and found["player_id"] in state["characters"]:
            char = state["characters"][found["player_id"]]
            you = dict(char)
            you["abilities"] = [
                {"id": a.id, "name": a.name, "focus_cost": a.focus_cost,
                 "stat": a.stat, "flavor": a.flavor, "target": a.target}
                for a in catalog.abilities_for(char["class_id"])
                if a.id in char["unlocked"]]
        order = state["turn"]["order"]
        active = order[state["turn"]["turn_index"]] if order else None
        return jsonify({
            "room": state["room"], "party": state["party"],
            "characters": state["characters"], "hazard": _public_hazard(state),
            "active_hazard_id": state["active_hazard_id"],
            "turn": {**state["turn"], "active_player_id": active},
            "conditions": state["conditions"],
            "phase": {"index": state["room"]["phase_index"],
                      "id": PHASES[state["room"]["phase_index"]][0],
                      "name": PHASES[state["room"]["phase_index"]][1],
                      "count": len(PHASES)},
            "you": you,
            "latest_seq": app.service._room(room_id).latest_seq(),
        })

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, threaded=True)
```

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/api/test_lobby_api.py -v`
Expected: PASS — 18 tests.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/api/
git commit -m "feat: Flask app factory, lobby API, and session-based seating"
```

---

## Task 12: Turn API

**Files:**
- Modify: `app.py` (add two routes inside `create_app`)
- Test: `tests/api/test_turn_api.py`

**Interfaces:**
- Consumes: `create_app`, `GameService.act`, `GameService.end_turn`.
- Produces: `POST /api/rooms/<id>/action` (body `{"ability_id", "target_id"?}`) and
  `POST /api/rooms/<id>/end-turn`. Both return `{"result": {...}, "events": [...]}`.
  Acting without a seat is `403`; acting illegally is `400` with the `RuleError` text.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_turn_api.py
import pytest
from tests.api.conftest import make_room


@pytest.fixture
def table(app):
    """A started two-player room; returns (room_id, ada_client, ben_client)."""
    ada, ben = app.test_client(), app.test_client()
    room_id = make_room(ada)
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ben.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ben", "class_id": "mechanical_technician"})
    ada.post(f"/api/rooms/{room_id}/start")
    return room_id, ada, ben


def test_action_returns_a_roll_result(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "unit_test_barrage"})
    assert response.status_code == 200
    result = response.get_json()["result"]
    assert 1 <= result["natural"] <= 20
    assert result["outcome"] in ("crit", "success", "failure", "fumble")


def test_action_returns_the_events_it_produced(table):
    room_id, ada, _ = table
    events = ada.post(f"/api/rooms/{room_id}/action",
                      json={"ability_id": "unit_test_barrage"}).get_json()["events"]
    assert any(e["kind"] == "action" for e in events)


def test_action_advances_the_turn_to_the_next_player(table):
    room_id, ada, ben = table
    ada.post(f"/api/rooms/{room_id}/action", json={"ability_id": "unit_test_barrage"})
    state = ben.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["turn"]["active_player_id"] == state["you"]["player_id"]


def test_acting_out_of_turn_is_a_400_with_a_readable_message(table):
    room_id, _, ben = table
    response = ben.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "shop_floor_fix"})
    assert response.status_code == 400
    assert "turn" in response.get_json()["error"].lower()


def test_acting_without_a_seat_is_a_403(app, table):
    room_id, _, _ = table
    stranger = app.test_client()
    response = stranger.post(f"/api/rooms/{room_id}/action",
                             json={"ability_id": "unit_test_barrage"})
    assert response.status_code == 403


def test_using_a_locked_ability_is_a_400(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "rubber_duck"})
    assert response.status_code == 400
    assert "unlocked" in response.get_json()["error"]


def test_using_an_unknown_ability_is_a_400(table):
    room_id, ada, _ = table
    response = ada.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "fireball"})
    assert response.status_code == 400


def test_action_without_an_ability_id_is_a_400(table):
    room_id, ada, _ = table
    assert ada.post(f"/api/rooms/{room_id}/action", json={}).status_code == 400


def test_end_turn_passes_the_turn(table):
    room_id, ada, ben = table
    assert ada.post(f"/api/rooms/{room_id}/end-turn").status_code == 200
    state = ben.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["turn"]["active_player_id"] == state["you"]["player_id"]


def test_end_turn_out_of_turn_is_a_400(table):
    room_id, _, ben = table
    assert ben.post(f"/api/rooms/{room_id}/end-turn").status_code == 400


def test_a_full_round_produces_a_hazard_attack_event(table):
    room_id, ada, ben = table
    ada.post(f"/api/rooms/{room_id}/end-turn")
    events = ben.post(f"/api/rooms/{room_id}/end-turn").get_json()["events"]
    assert any(e["kind"] == "hazard_attack" for e in events)


def test_focus_is_visibly_spent_after_acting(table):
    room_id, ada, _ = table
    before = ada.get(f"/api/rooms/{room_id}/state").get_json()["you"]["focus"]
    ada.post(f"/api/rooms/{room_id}/action", json={"ability_id": "unit_test_barrage"})
    after = ada.get(f"/api/rooms/{room_id}/state").get_json()["you"]["focus"]
    assert after == before - 1


def test_insufficient_focus_is_a_readable_400(app, table):
    room_id, ada, _ = table
    # Drain the active character's Focus directly. Looping real turns cannot exhaust it:
    # every phase-0 starter either regenerates as fast as it costs (unit_test_barrage, 1
    # Focus against +1/turn) or is once-per-hazard (binary_search_debug).
    room = app.service._room(room_id)
    state = room.load_state()
    active = state["turn"]["order"][state["turn"]["turn_index"]]
    state["characters"][active]["focus"] = 0
    room.save_state(state)

    response = ada.post(f"/api/rooms/{room_id}/action",
                        json={"ability_id": "binary_search_debug"})
    assert response.status_code == 400
    assert "Focus" in response.get_json()["error"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/api/test_turn_api.py -v`
Expected: FAIL — `404` on `/action`, so the status-code assertions fail.

- [ ] **Step 3: Add the routes**

Insert into `create_app`, immediately after `api_state`:

```python
    # --- the turn ----------------------------------------------------------

    @app.post("/api/rooms/<room_id>/action")
    def api_action(room_id):
        found = require_seat(room_id)
        if not found:
            return jsonify({"error": "you are not seated in this room"}), 403
        body = request.get_json(silent=True) or {}
        ability_id = body.get("ability_id")
        if not ability_id:
            raise ServiceError("no ability chosen")
        result = app.service.act(room_id, found["player_id"], ability_id,
                                 body.get("target_id"))
        events = result.pop("events", [])
        return jsonify({"result": result, "events": events})

    @app.post("/api/rooms/<room_id>/end-turn")
    def api_end_turn(room_id):
        found = require_seat(room_id)
        if not found:
            return jsonify({"error": "you are not seated in this room"}), 403
        outcome = app.service.end_turn(room_id, found["player_id"])
        return jsonify({"result": {"passed": True}, "events": outcome["events"]})
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/api/ -v`
Expected: PASS — 13 turn tests plus Task 11's 18.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/api/test_turn_api.py
git commit -m "feat: turn API for actions and passing"
```

---

## Task 13: SSE event stream

**Files:**
- Modify: `app.py` (add the stream route and a per-room broker)
- Create: `broker.py`
- Test: `tests/api/test_stream.py`

**Interfaces:**
- Consumes: `GameService.events_since`.
- Produces:
  - `broker.EventBroker` with `subscribe(room_id) -> queue.Queue`,
    `unsubscribe(room_id, q)`, `publish(room_id, payload: dict)`,
    `subscriber_count(room_id) -> int`.
  - `GET /api/rooms/<id>/stream` — `text/event-stream`, each frame
    `id: <seq>\nevent: <kind>\ndata: <json>\n\n`, honouring the `Last-Event-ID`
    request header and the `?since=` query parameter.
  - `app.broker` — the process-wide broker, also used by the narration worker (Task 15).

After this task the game is **fully playable end to end** with the template narrator
arriving in Task 14. That is the milestone worth stopping to play.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_stream.py
import json
import queue

import pytest

from broker import EventBroker
from tests.api.conftest import make_room


def test_broker_delivers_to_a_subscriber():
    broker = EventBroker()
    q = broker.subscribe("r1")
    broker.publish("r1", {"seq": 1, "kind": "action"})
    assert q.get(timeout=1)["seq"] == 1


def test_broker_delivers_to_every_subscriber():
    broker = EventBroker()
    a, b = broker.subscribe("r1"), broker.subscribe("r1")
    broker.publish("r1", {"seq": 1})
    assert a.get(timeout=1)["seq"] == 1 and b.get(timeout=1)["seq"] == 1


def test_broker_does_not_cross_rooms():
    broker = EventBroker()
    a = broker.subscribe("r1")
    broker.publish("r2", {"seq": 1})
    with pytest.raises(queue.Empty):
        a.get(timeout=0.1)


def test_unsubscribe_stops_delivery():
    broker = EventBroker()
    q = broker.subscribe("r1")
    broker.unsubscribe("r1", q)
    broker.publish("r1", {"seq": 1})
    assert broker.subscriber_count("r1") == 0


def test_publish_to_a_room_with_no_subscribers_is_safe():
    EventBroker().publish("nobody", {"seq": 1})


def test_stream_returns_the_event_stream_content_type(client):
    room_id = make_room(client)
    response = client.get(f"/api/rooms/{room_id}/stream?once=1")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")


def test_stream_replays_history_from_zero(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    body = client.get(f"/api/rooms/{room_id}/stream?once=1&since=0").get_data(as_text=True)
    assert "event: room_created" in body
    assert "event: player_joined" in body


def test_stream_honours_the_since_parameter(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    latest = client.get(f"/api/rooms/{room_id}/state").get_json()["latest_seq"]
    body = client.get(
        f"/api/rooms/{room_id}/stream?once=1&since={latest}").get_data(as_text=True)
    assert "event: player_joined" not in body


def test_stream_honours_the_last_event_id_header(client):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    latest = client.get(f"/api/rooms/{room_id}/state").get_json()["latest_seq"]
    body = client.get(f"/api/rooms/{room_id}/stream?once=1",
                      headers={"Last-Event-ID": str(latest)}).get_data(as_text=True)
    assert "event: player_joined" not in body


def test_replayed_frames_carry_ids_and_json_payloads(client):
    room_id = make_room(client)
    body = client.get(f"/api/rooms/{room_id}/stream?once=1&since=0").get_data(as_text=True)
    first = body.split("\n\n")[0].splitlines()
    assert first[0].startswith("id: ")
    assert first[1].startswith("event: ")
    payload = json.loads(first[2][len("data: "):])
    assert "seq" in payload and "kind" in payload


def test_stream_for_an_unknown_room_is_a_400(client):
    assert client.get("/api/rooms/zzzzzz/stream?once=1").status_code == 400
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/api/test_stream.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'broker'`

- [ ] **Step 3: Implement the broker**

```python
# broker.py
"""Process-wide fan-out of room events to connected SSE clients."""
from __future__ import annotations

import queue
import threading

_MAX_PENDING = 200


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict = {}
        self._guard = threading.Lock()

    def subscribe(self, room_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=_MAX_PENDING)
        with self._guard:
            self._subscribers.setdefault(room_id, []).append(q)
        return q

    def unsubscribe(self, room_id: str, q: queue.Queue) -> None:
        with self._guard:
            listeners = self._subscribers.get(room_id, [])
            if q in listeners:
                listeners.remove(q)
            if not listeners:
                self._subscribers.pop(room_id, None)

    def subscriber_count(self, room_id: str) -> int:
        with self._guard:
            return len(self._subscribers.get(room_id, []))

    def publish(self, room_id: str, payload: dict) -> None:
        with self._guard:
            listeners = list(self._subscribers.get(room_id, []))
        for q in listeners:
            try:
                q.put_nowait(payload)
            except queue.Full:
                # A client that cannot keep up is dropped; it will reconnect with
                # Last-Event-ID and replay whatever it missed.
                self.unsubscribe(room_id, q)
```

- [ ] **Step 4: Wire the stream into `app.py`**

Add `import json`, `import queue`, `from flask import Response, stream_with_context`,
and `from broker import EventBroker` to the imports. Inside `create_app`, after
`app.service = ...`:

```python
    app.broker = EventBroker()
```

Then add the route after `api_end_turn`:

```python
    # --- SSE ---------------------------------------------------------------

    def _frame(seq, kind, payload) -> str:
        body = json.dumps({"seq": seq, "kind": kind, **payload})
        return f"id: {seq}\nevent: {kind}\ndata: {body}\n\n"

    @app.get("/api/rooms/<room_id>/stream")
    def api_stream(room_id):
        app.service.snapshot(room_id)          # raises ServiceError -> 400 if unknown
        header = request.headers.get("Last-Event-ID")
        since = int(header or request.args.get("since") or 0)
        once = request.args.get("once") == "1"

        def generate():
            for event in app.service.events_since(room_id, since):
                yield _frame(event["seq"], event["kind"], event["payload"])
            if once:
                return
            q = app.broker.subscribe(room_id)
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        item = q.get(timeout=20)
                    except queue.Empty:
                        yield ": keep-alive\n\n"     # keeps proxies from timing out
                        continue
                    yield _frame(item["seq"], item["kind"], item)
            finally:
                app.broker.unsubscribe(room_id, q)

        return Response(stream_with_context(generate()),
                        mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})
```

Finally, publish from the service. In `service.py`, accept an optional broker and
publish every event after the commit — add `broker=None` to `GameService.__init__`,
store it, and add:

```python
    def _publish(self, room_id: str, events: list) -> None:
        if self.broker is None:
            return
        for event in events:
            self.broker.publish(room_id, {"seq": event["seq"], "kind": event["kind"],
                                          **event["payload"]})
```

Publish at the end of `act`, `end_turn`, `join_room`, and `start_game`, always
**after** `room.save_state(state)`. Each of those methods already knows the sequence
number it started from: in `act` use `self._publish(room_id, room.events_since(action_seq - 1))`;
in `end_turn` use the existing `start_seq`; in `join_room` and `start_game` capture
`start_seq = room.latest_seq()` before appending and publish from there.
In `app.py`, pass it through: `GameService(..., queue=..., broker=app.broker)` — which
means creating `app.broker` before `app.service`.

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/ -v`
Expected: PASS — 11 stream tests and every earlier test still green.

- [ ] **Step 6: Commit**

```bash
git add broker.py app.py service.py tests/api/test_stream.py
git commit -m "feat: SSE event stream with Last-Event-ID replay"
```

---

## Task 14: Narrator protocol, template fallback, and the post-filter

**Files:**
- Create: `narrator/base.py`, `narrator/fallback.py`, `narrator/filters.py`
- Test: `tests/narrator/test_fallback.py`, `tests/narrator/test_filters.py`

**Interfaces:**
- Consumes: nothing (narrator must not import `service` or `app`).
- Produces:
  - `base.Narrator` — a `typing.Protocol` with `name: str` and
    `narrate(job: dict) -> str`. Optional `stream(job) -> Iterator[str]`.
  - `base.FakeNarrator(text="...")` — deterministic, for tests.
  - `fallback.TemplateNarrator` — `name = "template"`, handles job kinds
    `"turn"`, `"phase"`, `"genesis"`.
  - `filters.clean(text: str, allowed_numbers: set[str], max_sentences: int = 4) -> str`
    — strips invented dice claims and contradicting numbers; returns `""` when nothing
    survives, which is the caller's signal to fall back.

- [ ] **Step 1: Write the failing tests**

```python
# tests/narrator/test_filters.py
from narrator.filters import clean


def test_plain_prose_passes_through():
    text = "The bracket holds. The team exhales."
    assert clean(text, {"7"}) == text


def test_invented_die_roll_sentence_is_dropped():
    text = "You roll a natural 20. The bracket holds."
    assert clean(text, {"7"}) == "The bracket holds."


def test_dc_claim_is_dropped():
    text = "That beats DC 13 easily. The harness is rerouted."
    assert clean(text, {"7"}) == "The harness is rerouted."


def test_d20_mention_is_dropped():
    assert clean("The d20 clatters. It works.", {"7"}) == "It works."


def test_number_matching_a_committed_fact_is_kept():
    text = "Severity drops by 7."
    assert clean(text, {"7"}) == text


def test_number_contradicting_the_facts_is_dropped():
    assert clean("Severity drops by 42.", {"7"}) == ""


def test_output_is_capped_at_four_sentences():
    text = " ".join(f"Sentence {w}." for w in
                    ["one", "two", "three", "four", "five", "six"])
    assert clean(text, set()).count(".") == 4


def test_assistant_preamble_is_stripped():
    text = "Sure! Here is the narration: The bracket holds."
    assert clean(text, set()) == "The bracket holds."


def test_wrapping_quotes_are_stripped():
    assert clean('"The bracket holds."', set()) == "The bracket holds."


def test_empty_input_returns_empty():
    assert clean("", set()) == ""


def test_all_sentences_rejected_returns_empty():
    assert clean("You rolled 19. DC was 12.", set()) == ""


def test_year_like_numbers_are_allowed_through():
    # Four-digit numbers read as dates or part numbers, not game state.
    assert clean("The 2019 edition of the standard applies.", set()) != ""
```

```python
# tests/narrator/test_fallback.py
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/narrator/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'narrator.filters'`

- [ ] **Step 3: Implement `narrator/base.py`**

```python
# narrator/base.py
"""The narrator seam. Everything above this line is facts; below it is prose."""
from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable


@runtime_checkable
class Narrator(Protocol):
    name: str

    def narrate(self, job: dict) -> str:
        """Return prose describing an already-committed outcome."""


class FakeNarrator:
    """Deterministic narrator for tests. Never touches a model."""

    name = "fake"

    def __init__(self, text: str = "The bracket holds.") -> None:
        self._text = text
        self.jobs: list = []

    def narrate(self, job: dict) -> str:
        self.jobs.append(job)
        return self._text

    def stream(self, job: dict) -> Iterator[str]:
        self.jobs.append(job)
        for word in self._text.split(" "):
            yield word + " "
```

- [ ] **Step 4: Implement `narrator/filters.py`**

```python
# narrator/filters.py
"""Strip anything the model invented that contradicts committed game state."""
from __future__ import annotations

import re

_SENTENCE = re.compile(r"[^.!?]+[.!?]")
_DICE_CLAIM = re.compile(
    r"\b(d20|dice|die|roll(s|ed|ing)?|natural\s+\d+|DC\s*\d+|modifier)\b",
    re.IGNORECASE)
_NUMBER = re.compile(r"\b\d+\b")
_PREAMBLE = re.compile(
    r"^\s*(sure|certainly|of course|here'?s?|here is|okay|ok)\b[^:]*:\s*",
    re.IGNORECASE)


def clean(text: str, allowed_numbers: set, max_sentences: int = 4) -> str:
    """Keep only sentences that invent neither a die roll nor a wrong number."""
    if not text:
        return ""
    text = _PREAMBLE.sub("", text.strip())
    text = text.strip().strip('"').strip("'").strip()

    sentences = [s.strip() for s in _SENTENCE.findall(text)] or \
        ([text.strip()] if text.strip() else [])
    allowed = {str(n) for n in allowed_numbers}

    kept = []
    for sentence in sentences:
        if _DICE_CLAIM.search(sentence):
            continue
        numbers = _NUMBER.findall(sentence)
        # Four-digit numbers read as years or part numbers, not game state.
        if any(n not in allowed and len(n) < 4 for n in numbers):
            continue
        kept.append(sentence)
        if len(kept) == max_sentences:
            break
    return " ".join(kept).strip()
```

- [ ] **Step 5: Implement `narrator/fallback.py`**

```python
# narrator/fallback.py
"""Template narrator. The game is fully playable with only this."""
from __future__ import annotations

_TURN = {
    "crit": [
        "{actor} does not merely solve {hazard} — {actor} dismantles the reason it existed. "
        "{ability} lands cleanly, and the room goes quiet in the way rooms do when "
        "something has just gone extremely right.",
        "{ability}, applied by {actor} with the confidence of someone who has been "
        "waiting weeks for exactly this. {hazard} simply stops being a problem.",
    ],
    "success": [
        "{actor} works {ability} against {hazard}. It gives, grudgingly, and the "
        "{phase} board moves one square closer to green.",
        "{ability} does what it says on the process document. {hazard} recedes, and "
        "{actor} writes it up before anyone can call it luck.",
        "There is no drama to it. {actor} applies {ability}, {hazard} yields, and the "
        "{phase} review gets one fewer open item.",
    ],
    "failure": [
        "{actor} tries {ability}. {hazard} absorbs it without comment and stays exactly "
        "where it was.",
        "{ability} was the right instinct and the wrong afternoon. {hazard} is unmoved, "
        "and the {phase} schedule notices.",
        "Nothing breaks. Nothing improves either. {hazard} outlasts {actor}'s {ability} "
        "and the team moves on to the next idea.",
    ],
    "fumble": [
        "{ability} goes wrong in a way that will be discussed for years. {hazard} gets "
        "worse, and {actor} is the one holding the multimeter when it does.",
        "{actor} reaches for {ability} and finds the one failure mode nobody modelled. "
        "{hazard} responds immediately and unkindly.",
    ],
}

_PHASE = [
    "The {phase} gate closes behind you. Whatever was true last week is a baseline now.",
    "{phase} begins. The problems change shape; the budget does not.",
]

_GENESIS = [
    'Programme "{room_name}": {hint}. The customer is enthusiastic, the schedule is '
    "fixed, and the requirements are still being written.",
    '"{room_name}" is {hint}. It has a launch date, a logo, and not yet a thermal model.',
]


def _pick(options: list, seq: int) -> str:
    """Rotate through the phrasings by sequence number.

    Deliberately not random: Random(n).choice() on a short list returns the same
    element for small consecutive n, which would make the narrator repeat itself.
    """
    return options[seq % len(options)]


class TemplateNarrator:
    """Deterministic per event, varied across events. Never claims a die roll."""

    name = "template"

    def narrate(self, job: dict) -> str:
        seq = int(job.get("event_seq", 0))
        kind = job.get("kind", "turn")

        if kind == "genesis":
            return _pick(_GENESIS, seq).format(
                room_name=job.get("room_name", "the programme"),
                hint=job.get("archetype_hint", "a complex engineering system"))

        if kind == "phase":
            return _pick(_PHASE, seq).format(phase=job.get("phase", "the next phase"))

        hazard = job.get("hazard") or {}
        return _pick(_TURN[job.get("outcome", "success")], seq).format(
            actor=job.get("actor_name", "The engineer"),
            ability=job.get("ability_name", "the obvious approach"),
            hazard=hazard.get("name", "the problem"),
            phase=job.get("phase", "current"))
```

- [ ] **Step 6: Run to verify they pass**

Run: `pytest tests/narrator/ -v`
Expected: PASS — 12 filter tests and 11 fallback tests.

- [ ] **Step 7: Commit**

```bash
git add narrator/base.py narrator/fallback.py narrator/filters.py tests/narrator/
git commit -m "feat: narrator protocol, template fallback, and fact-contradiction filter"
```

---

## Task 15: Narration queue and worker

**Files:**
- Create: `narrator/queue_.py`, `narrator/worker.py`
- Modify: `app.py` (start the worker unless `TESTING`)
- Test: `tests/narrator/test_queue.py`, `tests/narrator/test_worker.py`

**Interfaces:**
- Consumes: `Narrator`, `FakeNarrator`, `TemplateNarrator`, `filters.clean`,
  `RoomDB`, `EventBroker`.
- Produces:
  - `queue_.NarrationQueue` with `submit(job: dict)`, `get(timeout) -> dict | None`,
    `pending() -> int`, `drop_room(room_id)`. Lower `priority` wins; ties break by
    submission order. (`turn` = 0, `phase` = 1, `genesis` = 2.)
  - `worker.NarrationWorker(queue, narrator, service, broker, fallback=None,
    deadline=45.0)` with `start()`, `stop()`, `run_once() -> bool`.
  - The worker's contract: write `narrations` status, append a **real `narration`
    event** to the room log (so reconnecting clients replay prose for free), and
    publish it on the broker.

- [ ] **Step 1: Write the failing tests**

```python
# tests/narrator/test_queue.py
import pytest
from narrator.queue_ import NarrationQueue


def test_get_on_an_empty_queue_returns_none():
    assert NarrationQueue().get(timeout=0.01) is None


def test_submitted_job_comes_back():
    q = NarrationQueue()
    q.submit({"kind": "turn", "priority": 0, "room_id": "r"})
    assert q.get(timeout=1)["kind"] == "turn"


def test_lower_priority_number_is_served_first():
    q = NarrationQueue()
    q.submit({"kind": "genesis", "priority": 2, "room_id": "r"})
    q.submit({"kind": "turn", "priority": 0, "room_id": "r"})
    assert q.get(timeout=1)["kind"] == "turn"


def test_equal_priority_is_first_in_first_out():
    q = NarrationQueue()
    q.submit({"kind": "turn", "priority": 0, "room_id": "r", "n": 1})
    q.submit({"kind": "turn", "priority": 0, "room_id": "r", "n": 2})
    assert [q.get(timeout=1)["n"], q.get(timeout=1)["n"]] == [1, 2]


def test_pending_counts_waiting_jobs():
    q = NarrationQueue()
    q.submit({"kind": "turn", "priority": 0, "room_id": "r"})
    q.submit({"kind": "turn", "priority": 0, "room_id": "r"})
    assert q.pending() == 2


def test_drop_room_discards_only_that_rooms_jobs():
    q = NarrationQueue()
    q.submit({"kind": "turn", "priority": 0, "room_id": "a"})
    q.submit({"kind": "turn", "priority": 0, "room_id": "b"})
    q.drop_room("a")
    assert q.get(timeout=1)["room_id"] == "b"
    assert q.get(timeout=0.01) is None


def test_job_without_a_priority_defaults_to_normal():
    q = NarrationQueue()
    q.submit({"kind": "turn", "room_id": "r"})
    assert q.get(timeout=1) is not None
```

```python
# tests/narrator/test_worker.py
import pytest
from broker import EventBroker
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from narrator.base import FakeNarrator
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker
from service import GameService


class BoomNarrator:
    name = "boom"

    def narrate(self, job):
        raise RuntimeError("model exploded")


class SlowNarrator:
    name = "slow"

    def narrate(self, job):
        import time
        time.sleep(0.3)
        return "Eventually, prose."


@pytest.fixture
def rig(tmp_path):
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes())
    room_id = svc.create_room("Kestrel", "aircraft")
    a = svc.join_room(room_id, "Ada", "computer_scientist")
    svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    result = svc.act(room_id, a["player_id"], "unit_test_barrage")
    return svc, room_id, result["event_seq"]


def make_job(room_id, seq):
    return {"kind": "turn", "priority": 0, "room_id": room_id, "event_seq": seq,
            "actor_name": "Ada", "actor_class": "Computer Scientist",
            "ability_name": "Unit Test Barrage", "outcome": "success",
            "natural": 12, "total": 15, "dc": 13, "phase": "Requirements",
            "premise": "A trainer aircraft", "hazard": None, "changes": []}


def test_run_once_on_an_empty_queue_returns_false(rig):
    svc, _, _ = rig
    worker = NarrationWorker(NarrationQueue(), FakeNarrator(), svc, EventBroker())
    assert worker.run_once(timeout=0.01) is False


def test_worker_writes_the_narration_to_the_room(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator("The suite goes green."), svc,
                    EventBroker()).run_once(timeout=1)
    stored = svc.narration(room_id, seq)
    assert stored["status"] == "done"
    assert stored["text"] == "The suite goes green."
    assert stored["source"] == "fake"


def test_worker_appends_a_narration_event_so_replay_works(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator(), svc, EventBroker()).run_once(timeout=1)
    kinds = [e["kind"] for e in svc.events_since(room_id, 0)]
    assert "narration" in kinds


def test_narration_event_references_its_action(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator(), svc, EventBroker()).run_once(timeout=1)
    event = [e for e in svc.events_since(room_id, 0) if e["kind"] == "narration"][0]
    assert event["payload"]["event_seq"] == seq


def test_worker_publishes_to_the_broker(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator(), svc, broker).run_once(timeout=1)
    # Assert a narration was published, not that it was FIRST: once Task 21 adds
    # token streaming, narration_chunk events precede it on the same broker.
    kinds = []
    while True:
        try:
            kinds.append(sub.get(timeout=0.3)["kind"])
        except Exception:
            break
    assert "narration" in kinds


def test_model_failure_falls_back_to_the_template(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, BoomNarrator(), svc, EventBroker()).run_once(timeout=1)
    stored = svc.narration(room_id, seq)
    assert stored["status"] == "done"
    assert stored["source"] == "template"
    assert stored["text"].strip()


def test_exceeding_the_deadline_falls_back_to_the_template(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, SlowNarrator(), svc, EventBroker(),
                    deadline=0.05).run_once(timeout=1)
    assert svc.narration(room_id, seq)["source"] == "template"


def test_filter_rejecting_everything_falls_back_to_the_template(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, FakeNarrator("You rolled a natural 20. DC 13 beaten."),
                    svc, EventBroker()).run_once(timeout=1)
    assert svc.narration(room_id, seq)["source"] == "template"


def test_worker_thread_starts_and_stops_cleanly(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    worker = NarrationWorker(q, FakeNarrator(), svc, EventBroker())
    worker.start()
    q.submit(make_job(room_id, seq))
    import time
    for _ in range(50):
        if svc.narration(room_id, seq)["status"] == "done":
            break
        time.sleep(0.02)
    worker.stop()
    assert svc.narration(room_id, seq)["status"] == "done"
    assert worker.thread is None or not worker.thread.is_alive()
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/narrator/test_queue.py tests/narrator/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'narrator.queue_'`

The module is `queue_.py`, with a trailing underscore, so it never shadows the stdlib
`queue` module that `broker.py` and the worker both import.

- [ ] **Step 3: Implement `narrator/queue_.py`**

```python
# narrator/queue_.py
"""Priority queue of narration jobs. Lower priority number is served first."""
from __future__ import annotations

import heapq
import itertools
import threading

PRIORITY = {"turn": 0, "phase": 1, "genesis": 2}


class NarrationQueue:
    def __init__(self) -> None:
        self._heap: list = []
        self._counter = itertools.count()
        self._guard = threading.Condition()

    def submit(self, job: dict) -> None:
        priority = job.get("priority", PRIORITY.get(job.get("kind", "turn"), 1))
        with self._guard:
            heapq.heappush(self._heap, (priority, next(self._counter), job))
            self._guard.notify()

    def get(self, timeout: float = 1.0) -> "dict | None":
        with self._guard:
            if not self._heap:
                self._guard.wait(timeout)
            if not self._heap:
                return None
            return heapq.heappop(self._heap)[2]

    def pending(self) -> int:
        with self._guard:
            return len(self._heap)

    def drop_room(self, room_id: str) -> None:
        with self._guard:
            self._heap = [item for item in self._heap
                          if item[2].get("room_id") != room_id]
            heapq.heapify(self._heap)
```

- [ ] **Step 4: Implement `narrator/worker.py`**

```python
# narrator/worker.py
"""One background thread. One generation at a time. Never blocks a turn."""
from __future__ import annotations

import concurrent.futures
import logging
import threading

from narrator.fallback import TemplateNarrator
from narrator.filters import clean

log = logging.getLogger(__name__)


def _allowed_numbers(job: dict) -> set:
    """Every number the engine actually committed, as strings."""
    allowed = set()
    hazard = job.get("hazard") or {}
    for value in (hazard.get("severity"), hazard.get("max_severity")):
        if value is not None:
            allowed.add(str(value))
    for change in job.get("changes", []):
        for key in ("amount", "value", "severity", "stamina", "focus", "count"):
            if key in change:
                allowed.add(str(change[key]))
    return allowed


class NarrationWorker:
    def __init__(self, queue, narrator, service, broker, fallback=None,
                 deadline: float = 45.0) -> None:
        self.queue = queue
        self.narrator = narrator
        self.service = service
        self.broker = broker
        self.fallback = fallback or TemplateNarrator()
        self.deadline = deadline
        self.thread: "threading.Thread | None" = None
        self._stop = threading.Event()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="narrator",
                                       daemon=True)
        self.thread.start()

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=join_timeout)
            self.thread = None
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once(timeout=0.5)

    # --- one job -----------------------------------------------------------

    def run_once(self, timeout: float = 1.0) -> bool:
        job = self.queue.get(timeout=timeout)
        if job is None:
            return False
        try:
            self._handle(job)
        except Exception:                       # a bad job must not kill the worker
            log.exception("narration job failed: %s", job.get("kind"))
        return True

    def _generate(self, job: dict) -> tuple:
        """Return (text, source). Falls back on error, timeout, or empty output."""
        try:
            future = self._pool.submit(self.narrator.narrate, job)
            raw = future.result(timeout=self.deadline)
            text = clean(raw, _allowed_numbers(job))
            if text:
                return text, self.narrator.name
            log.info("narration filtered to nothing; using template")
        except concurrent.futures.TimeoutError:
            log.warning("narration exceeded %.0fs deadline; using template",
                        self.deadline)
        except Exception:
            log.exception("narrator raised; using template")
        return self.fallback.narrate(job), self.fallback.name

    def _handle(self, job: dict) -> None:
        room_id, event_seq = job["room_id"], job.get("event_seq")
        room = self.service._room(room_id)
        if event_seq is not None:
            room.update_narration(event_seq, "streaming", "", "")

        text, source = self._generate(job)

        if event_seq is not None:
            room.update_narration(event_seq, "done", text, source)
        payload = {"event_seq": event_seq, "text": text, "source": source,
                   "job_kind": job.get("kind", "turn")}
        seq = room.append_event("narration", None, payload)
        self.broker.publish(room_id, {"seq": seq, "kind": "narration", **payload})
```

- [ ] **Step 5: Start the worker in `app.py`**

Inside `create_app`, after `app.service` is built:

```python
    from narrator.fallback import TemplateNarrator
    from narrator.queue_ import NarrationQueue
    from narrator.worker import NarrationWorker

    app.narration_queue = app.config.get("NARRATION")
    if app.narration_queue is None and not app.config.get("TESTING"):
        app.narration_queue = NarrationQueue()
        app.narrator = TemplateNarrator()        # Task 16 swaps in the real model
        app.worker = NarrationWorker(app.narration_queue, app.narrator,
                                     app.service, app.broker)
        app.worker.start()
    app.service.queue = app.narration_queue
```

- [ ] **Step 6: Run to verify they pass**

Run: `pytest tests/ -v`
Expected: PASS — 7 queue tests, 9 worker tests, everything earlier still green.

- [ ] **Step 7: Commit**

```bash
git add narrator/queue_.py narrator/worker.py app.py tests/narrator/test_queue.py tests/narrator/test_worker.py
git commit -m "feat: narration priority queue and single background worker"
```

---

## Task 16: Prompts, the Llama binding, and the model download

**Files:**
- Create: `narrator/prompts.py`, `narrator/llm.py`, `scripts/setup_model.py`
- Test: `tests/narrator/test_prompts.py`, `tests/narrator/test_llm.py`,
  `tests/narrator/test_setup_model.py`

**Interfaces:**
- Consumes: `Narrator` protocol.
- Produces:
  - `prompts.SYSTEM_PROMPT: str`; `prompts.build(job: dict) -> list[dict]` returning
    chat messages `[{"role": "system", ...}, {"role": "user", ...}]`.
  - `prompts.SAMPLING: dict` — `{"temperature": 0.8, "max_tokens": 140,
    "stop": ["\n\n"]}` for turn/phase jobs; genesis jobs use `max_tokens=220`.
  - `llm.LlamaNarrator(model_path, loader=None, n_ctx=4096, n_threads=2)` with
    `name = "llm"`, lazy `.load()`, `.narrate(job) -> str`, `.available -> bool`.
    `loader` is an injection seam: a callable `(path, **kw) -> model` so tests never
    need `llama-cpp-python`.
  - `llm.ModelUnavailable(Exception)`.
  - `setup_model.resolve_source(has_token: bool) -> tuple[str, str]` — `(repo_id, filename)`.
  - `setup_model.MODEL_FILENAME`, `setup_model.MIRROR_REPO`, `setup_model.OFFICIAL_REPO`.
  - `setup_model.main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/narrator/test_prompts.py
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
```

```python
# tests/narrator/test_llm.py
import pytest
from narrator.llm import LlamaNarrator, ModelUnavailable


class StubModel:
    def __init__(self, text="The bracket holds.", raise_on_call=False):
        self.text = text
        self.raise_on_call = raise_on_call
        self.calls = []

    def create_chat_completion(self, messages, **kwargs):
        if self.raise_on_call:
            raise RuntimeError("inference failed")
        self.calls.append((messages, kwargs))
        return {"choices": [{"message": {"content": self.text}}]}


def loader_for(model):
    def _load(path, **kwargs):
        _load.kwargs = kwargs
        return model
    return _load


def turn_job():
    return {"kind": "turn", "premise": "p", "phase": "Design",
            "actor_name": "Ada", "actor_class": "Computer Scientist",
            "ability_name": "Refactor", "outcome": "success", "natural": 12,
            "total": 15, "dc": 13, "hazard": None, "changes": []}


def test_name_identifies_the_source(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    assert LlamaNarrator(str(path), loader=loader_for(StubModel())).name == "llm"


def test_available_is_false_when_the_file_is_missing(tmp_path):
    assert LlamaNarrator(str(tmp_path / "absent.gguf")).available is False


def test_available_is_true_when_the_file_exists(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    assert LlamaNarrator(str(path), loader=loader_for(StubModel())).available is True


def test_narrate_without_a_model_file_raises(tmp_path):
    with pytest.raises(ModelUnavailable):
        LlamaNarrator(str(tmp_path / "absent.gguf")).narrate(turn_job())


def test_narrate_returns_the_model_text(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    narrator = LlamaNarrator(str(path),
                             loader=loader_for(StubModel("It holds.")))
    assert narrator.narrate(turn_job()) == "It holds."


def test_model_is_loaded_once_and_reused(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    model = StubModel()
    narrator = LlamaNarrator(str(path), loader=loader_for(model))
    narrator.narrate(turn_job())
    narrator.narrate(turn_job())
    assert len(model.calls) == 2          # two generations, one load


def test_loader_receives_the_cpu_tuned_settings(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    load = loader_for(StubModel())
    LlamaNarrator(str(path), loader=load, n_ctx=4096, n_threads=2).narrate(turn_job())
    assert load.kwargs["n_ctx"] == 4096 and load.kwargs["n_threads"] == 2


def test_inference_failure_propagates_for_the_worker_to_catch(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    narrator = LlamaNarrator(str(path),
                             loader=loader_for(StubModel(raise_on_call=True)))
    with pytest.raises(RuntimeError):
        narrator.narrate(turn_job())


def test_genesis_job_gets_a_larger_token_budget(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    model = StubModel()
    LlamaNarrator(str(path), loader=loader_for(model)).narrate(
        {"kind": "genesis", "room_name": "Kestrel", "archetype_hint": "a car"})
    assert model.calls[0][1]["max_tokens"] > 140
```

```python
# tests/narrator/test_setup_model.py
from scripts.setup_model import (MIRROR_REPO, MODEL_FILENAME, OFFICIAL_REPO,
                                 resolve_source)


def test_filename_is_the_q4_k_m_quantisation():
    assert MODEL_FILENAME == "Llama-3.2-1B-Instruct-Q4_K_M.gguf"


def test_without_a_token_the_ungated_mirror_is_used():
    repo, filename = resolve_source(has_token=False)
    assert repo == MIRROR_REPO and filename == MODEL_FILENAME


def test_with_a_token_the_official_gated_repo_is_preferred():
    repo, _ = resolve_source(has_token=True)
    assert repo == OFFICIAL_REPO


def test_official_repo_is_the_meta_one():
    assert OFFICIAL_REPO == "meta-llama/Llama-3.2-1B-Instruct"
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/narrator/test_prompts.py tests/narrator/test_llm.py tests/narrator/test_setup_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'narrator.prompts'`

Add `touch scripts/__init__.py` so `scripts.setup_model` is importable.

- [ ] **Step 3: Implement `narrator/prompts.py`**

```python
# narrator/prompts.py
"""Prompt construction. The engine decided everything; the model only describes it."""
from __future__ import annotations

SYSTEM_PROMPT = (
    "You are the Game Master of an engineering RPG. Describe what just happened "
    "in 2-4 sentences of vivid, technically literate prose.\n"
    "RULES: Never invent numbers. Never contradict the stated outcome. "
    "Never decide what happens next. Do not address the player as \"you the user\". "
    "Do not mention dice, rolls, or difficulty classes. Write only the prose."
)

SAMPLING = {"temperature": 0.8, "max_tokens": 140, "stop": ["\n\n"]}
GENESIS_SAMPLING = {"temperature": 0.9, "max_tokens": 220, "stop": ["\n\n\n"]}

_OUTCOME_LABEL = {"crit": "CRITICAL SUCCESS", "success": "SUCCESS",
                  "failure": "FAILURE", "fumble": "FUMBLE"}


def _turn_body(job: dict) -> str:
    hazard = job.get("hazard") or {}
    lines = [
        f"PROJECT: {job.get('premise') or 'an unnamed engineering programme'}",
        f"PHASE: {job.get('phase', 'Design')}",
    ]
    if hazard:
        lines.append(
            f"PROBLEM: {hazard.get('name')} "
            f"(severity {hazard.get('severity')}/{hazard.get('max_severity')})")
    lines += [
        f"ENGINEER: {job.get('actor_name')}, {job.get('actor_class')}",
        f"ACTION: {job.get('ability_name')}",
        f"OUTCOME: {_OUTCOME_LABEL.get(job.get('outcome'), 'SUCCESS')}",
    ]
    for change in job.get("changes", []):
        if change.get("kind") == "hazard_damage":
            lines.append(f"EFFECT: the problem was reduced by {change['amount']}")
        elif change.get("kind") == "party_delta":
            lines.append(
                f"EFFECT: {change['field'].replace('_', ' ')} changed by "
                f"{change['delta']}")
        elif change.get("kind") == "stress":
            lines.append(f"EFFECT: an engineer took {change['amount']} stress")
    return "\n".join(lines)


def build(job: dict) -> list:
    kind = job.get("kind", "turn")

    if kind == "genesis":
        body = (
            f"Invent a short engineering programme brief.\n"
            f"PROGRAMME NAME: {job.get('room_name')}\n"
            f"SYSTEM: {job.get('archetype_hint')}\n"
            "Write 2-3 sentences naming the customer and one distinctive, "
            "difficult constraint. Do not use bullet points."
        )
    elif kind == "hazards":
        existing = ", ".join(job.get("existing", []))
        body = (
            f"PROJECT: {job.get('premise')}\n"
            f"PHASE: {job.get('phase')}\n"
            f"List exactly {job.get('count')} engineering problems that could "
            f"derail this phase.\n"
            f"Format each on its own line as: Name - one sentence description\n"
            f"Do not number them. Avoid repeating: {existing or 'nothing'}"
        )
    elif kind == "phase":
        body = (
            f"PROJECT: {job.get('premise')}\n"
            f"The team has just entered the {job.get('phase')} phase.\n"
            "Write 2-3 sentences marking the transition."
        )
    else:
        body = _turn_body(job)

    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": body}]
```

- [ ] **Step 4: Implement `narrator/llm.py`**

```python
# narrator/llm.py
"""llama-cpp-python binding. Loaded lazily, inside the worker thread."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from narrator.prompts import GENESIS_SAMPLING, SAMPLING, build

log = logging.getLogger(__name__)


class ModelUnavailable(Exception):
    """The GGUF file is missing, or llama-cpp-python is not installed."""


def _default_loader(path: str, **kwargs):
    try:
        from llama_cpp import Llama
    except ImportError as exc:                  # optional dependency by design
        raise ModelUnavailable(
            "llama-cpp-python is not installed; run `make install-llm`") from exc
    return Llama(model_path=path, verbose=False, **kwargs)


class LlamaNarrator:
    name = "llm"

    def __init__(self, model_path: str, loader=None, n_ctx: int = 4096,
                 n_threads: int = 2, n_batch: int = 256) -> None:
        self.model_path = Path(model_path)
        self._loader = loader or _default_loader
        self._settings = {"n_ctx": n_ctx, "n_threads": n_threads,
                          "n_batch": n_batch}
        self._model = None
        self._guard = threading.Lock()

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def load(self):
        with self._guard:
            if self._model is None:
                if not self.available:
                    raise ModelUnavailable(f"no model at {self.model_path}")
                log.info("loading %s", self.model_path.name)
                self._model = self._loader(str(self.model_path), **self._settings)
            return self._model

    def narrate(self, job: dict) -> str:
        model = self.load()
        sampling = GENESIS_SAMPLING if job.get("kind") in ("genesis", "hazards") \
            else SAMPLING
        response = model.create_chat_completion(messages=build(job), **sampling)
        return response["choices"][0]["message"]["content"].strip()
```

- [ ] **Step 5: Implement `scripts/setup_model.py`**

```python
# scripts/setup_model.py
"""Download the Llama 3.2 1B GGUF. Idempotent; safe to run repeatedly."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MODEL_FILENAME = "Llama-3.2-1B-Instruct-Q4_K_M.gguf"
MIRROR_REPO = "bartowski/Llama-3.2-1B-Instruct-GGUF"
OFFICIAL_REPO = "meta-llama/Llama-3.2-1B-Instruct"
MODEL_DIR = Path("models")
MIN_BYTES = 600 * 1024 * 1024          # a valid Q4_K_M 1B is ~0.8 GB


def resolve_source(has_token: bool) -> tuple:
    """Meta's repo is gated; without HF_TOKEN use the ungated mirror."""
    return (OFFICIAL_REPO if has_token else MIRROR_REPO), MODEL_FILENAME


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                       help="re-download even if the file is already present")
    parser.add_argument("--dest", default=str(MODEL_DIR))
    args = parser.parse_args(argv)

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / MODEL_FILENAME

    if target.exists() and not args.force:
        size = target.stat().st_size
        if size >= MIN_BYTES:
            print(f"already present: {target} ({size / 1e9:.2f} GB)")
            return 0
        print(f"{target} is only {size} bytes; re-downloading")

    token = os.environ.get("HF_TOKEN")
    repo_id, filename = resolve_source(bool(token))
    print(f"downloading {filename} from {repo_id} ...")

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("huggingface_hub is missing; run `make install`", file=sys.stderr)
        return 1

    try:
        path = hf_hub_download(repo_id=repo_id, filename=filename,
                               local_dir=str(dest_dir), token=token)
    except Exception as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        if repo_id == OFFICIAL_REPO:
            print("The official repo is gated. Accept the licence on Hugging Face, "
                  "or unset HF_TOKEN to use the ungated mirror.", file=sys.stderr)
        return 1

    size = Path(path).stat().st_size
    if size < MIN_BYTES:
        print(f"downloaded file is suspiciously small ({size} bytes)",
              file=sys.stderr)
        return 1
    print(f"done: {path} ({size / 1e9:.2f} GB)")
    print("The game runs without this file; it only improves the prose.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Use the real narrator in `app.py`**

Replace the `TemplateNarrator()` line added in Task 15 with a model-aware choice:

```python
        from narrator.llm import LlamaNarrator
        model_path = app.config.get(
            "MODEL_PATH", "models/Llama-3.2-1B-Instruct-Q4_K_M.gguf")
        llama = LlamaNarrator(model_path)
        app.narrator = llama if llama.available else TemplateNarrator()
        app.config["NARRATOR_NAME"] = app.narrator.name
```

and expose it to the UI by adding `"narrator": app.config.get("NARRATOR_NAME", "template")`
to the `api_state` response, so the page can show `DM: local model` or `DM: template`.

- [ ] **Step 7: Run to verify they pass**

Run: `pytest tests/ -v`
Expected: PASS — 11 prompt tests, 9 LLM tests, 4 setup tests, everything else green.
The suite must pass with `llama-cpp-python` **not** installed.

- [ ] **Step 8: Commit**

```bash
git add narrator/prompts.py narrator/llm.py scripts/ app.py tests/narrator/
git commit -m "feat: Llama 3.2 1B narrator, prompt discipline, and model download script"
```

---

## Task 17: Genesis

**Files:**
- Create: `narrator/genesis.py`
- Modify: `service.py` (two apply methods + genesis enqueue), `narrator/worker.py`
  (handle the two genesis job kinds)
- Test: `tests/narrator/test_genesis.py`

**Interfaces:**
- Consumes: `GameService`, `NarrationQueue`, `PHASES`.
- Produces:
  - `genesis.parse_hazard_lines(text: str, expected: int) -> list[tuple[str, str]]`
    — line-based parsing, deliberately not JSON; a 1B model produces reliable
    `Name - description` lines and unreliable JSON.
  - `genesis.genesis_jobs(room_id, state, archetype) -> list[dict]` — one premise job
    plus one hazards job per phase, all at low priority.
  - `GameService.set_premise(room_id, text)`.
  - `GameService.rename_hazards(room_id, phase_index, entries)` — rewrites `name` and
    `description` in place for undefeated hazards only; severity, DC, attack type, and
    weakness are never touched by the model.
  - `GameService.enqueue_genesis(room_id)` — called at the end of `create_room`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/narrator/test_genesis.py
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/narrator/test_genesis.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'narrator.genesis'`

- [ ] **Step 3: Implement `narrator/genesis.py`**

```python
# narrator/genesis.py
"""Room genesis: the programme premise and a rewrite of every hazard's prose.

Runs at low priority while players are still joining, so the slowest generation
hides inside lobby time. It rewrites names and descriptions only — never mechanics.
"""
from __future__ import annotations

import re

from engine.phases import PHASES

# A dash needs space on BOTH sides so hyphenated names survive ("Tolerance Stack-Up");
# a colon needs space only after it, because "Name: description" is what models emit.
_SEPARATOR = re.compile(r"\s+[-–—]\s+|\s*:\s+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_MAX_NAME = 60


def parse_hazard_lines(text: str, expected: int) -> list:
    """Parse 'Name - description' lines. Line-based beats JSON for a 1B model."""
    out: list = []
    for raw in (text or "").splitlines():
        line = _BULLET.sub("", raw.strip())
        if not line:
            continue
        parts = _SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        name, description = parts[0].strip(), parts[1].strip()
        if not name or not description or len(name) > _MAX_NAME:
            continue
        out.append((name, description))
        if len(out) == expected:
            break
    return out


def genesis_jobs(room_id: str, state: dict, archetype: dict) -> list:
    """One premise job, then one hazard-rewrite job per phase."""
    jobs = [{
        "kind": "genesis", "priority": 2, "room_id": room_id, "event_seq": None,
        "room_name": state["room"]["name"],
        "archetype_hint": archetype.get("hint", "a complex engineering system"),
    }]
    for phase_index, (_, phase_name) in enumerate(PHASES):
        in_phase = [h for h in state["hazards"]
                    if h["phase_index"] == phase_index and not h["is_boss"]]
        if not in_phase:
            continue
        jobs.append({
            "kind": "hazards", "priority": 3, "room_id": room_id,
            "event_seq": None, "phase_index": phase_index, "phase": phase_name,
            "count": len(in_phase),
            "existing": [h["name"] for h in in_phase],
            "premise": state["room"].get("premise", ""),
        })
    return jobs
```

- [ ] **Step 4: Add the service methods**

Append to `service.py`, and import `genesis_jobs` at the top:

```python
    def set_premise(self, room_id: str, text: str) -> None:
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            state["room"]["premise"] = text.strip()
            room.save_state(state)

    def rename_hazards(self, room_id: str, phase_index: int, entries: list) -> None:
        """Rewrite prose only. Severity, DC, attack type and weakness are untouched."""
        if not entries:
            return
        with self._lock(room_id):
            room = self._room(room_id)
            state = room.load_state()
            targets = [h for h in state["hazards"]
                       if h["phase_index"] == phase_index
                       and not h["is_boss"] and not h["defeated"]]
            for hazard, (name, description) in zip(targets, entries):
                hazard["name"] = name
                hazard["description"] = description
            room.save_state(state)

    def enqueue_genesis(self, room_id: str) -> None:
        if self.queue is None:
            return
        state = self.snapshot(room_id)
        archetype = self.archetypes[state["room"]["archetype"]]
        for job in genesis_jobs(room_id, state, archetype):
            self.queue.submit(job)
```

Call `self.enqueue_genesis(room_id)` as the last line of `create_room`, after
`self._reindex(...)`.

- [ ] **Step 5: Teach the worker the two genesis kinds**

In `narrator/worker.py`, replace the body of `_handle` with a dispatch:

```python
    def _handle(self, job: dict) -> None:
        kind = job.get("kind", "turn")
        if kind == "genesis":
            return self._handle_premise(job)
        if kind == "hazards":
            return self._handle_hazards(job)
        return self._handle_turn(job)

    def _handle_turn(self, job: dict) -> None:
        room_id, event_seq = job["room_id"], job.get("event_seq")
        room = self.service._room(room_id)
        if event_seq is not None:
            room.update_narration(event_seq, "streaming", "", "")
        text, source = self._generate(job)
        if event_seq is not None:
            room.update_narration(event_seq, "done", text, source)
        payload = {"event_seq": event_seq, "text": text, "source": source,
                   "job_kind": job.get("kind", "turn")}
        seq = room.append_event("narration", None, payload)
        self.broker.publish(room_id, {"seq": seq, "kind": "narration", **payload})

    def _handle_premise(self, job: dict) -> None:
        text, source = self._generate(job)
        self.service.set_premise(job["room_id"], text)
        room = self.service._room(job["room_id"])
        payload = {"premise": text, "source": source}
        seq = room.append_event("premise", None, payload)
        self.broker.publish(job["room_id"],
                            {"seq": seq, "kind": "premise", **payload})

    def _handle_hazards(self, job: dict) -> None:
        from narrator.genesis import parse_hazard_lines
        try:
            raw = self._pool.submit(self.narrator.narrate, job).result(
                timeout=self.deadline)
            entries = parse_hazard_lines(raw, job["count"])
        except Exception:
            log.info("hazard generation failed; keeping template hazards")
            return                      # the deterministic hazards stay in place
        if not entries:
            return
        self.service.rename_hazards(job["room_id"], job["phase_index"], entries)
        room = self.service._room(job["room_id"])
        payload = {"phase_index": job["phase_index"],
                   "names": [n for n, _ in entries]}
        seq = room.append_event("campaign_updated", None, payload)
        self.broker.publish(job["room_id"],
                            {"seq": seq, "kind": "campaign_updated", **payload})
```

Note the asymmetry, and it is deliberate: a failed *hazard* rewrite silently keeps the
deterministic template hazards, because the game is already playable with them. A failed
*turn* narration falls back to template prose, because a turn must always produce text.

- [ ] **Step 6: Run to verify they pass**

Run: `pytest tests/ -v`
Expected: PASS — 15 genesis tests and the full suite.

- [ ] **Step 7: Commit**

```bash
git add narrator/genesis.py narrator/worker.py service.py tests/narrator/test_genesis.py
git commit -m "feat: room genesis writes the premise and rewrites hazard prose"
```

---

## Task 18: Theme, lobby page, and character select

**Files:**
- Create: `static/css/theme.css`, `templates/base.html`, `templates/lobby.html`,
  `templates/join.html`, `static/js/lobby.js`
- Modify: `app.py` (HTML routes and the QR endpoint)
- Test: `tests/api/test_pages.py`

**Interfaces:**
- Consumes: the JSON API from Tasks 11–13.
- Produces:
  - `GET /` → lobby page. `GET /room/<id>/join` → character select.
    `GET /room/<id>` → the table (template added in Task 19).
    `GET /room/<id>/qr.svg` → an SVG QR code of the room's join URL.
  - CSS custom properties on `:root`: `--ground`, `--panel`, `--line`, `--ink`,
    `--ink-dim`, `--cyan`, `--amber`, `--red`, `--green`, `--mono`, `--sans`.

**Design direction** (spec §6.1): an engineering review room, not a tavern. Dark slate
ground, blueprint-cyan and amber accents, a faint drafting grid, monospace for every
number and a humanist sans for prose. Technical Debt is the only red thing on screen.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_pages.py
from tests.api.conftest import make_room


def test_lobby_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Critical Path" in response.data


def test_lobby_page_is_html(client):
    assert client.get("/").headers["Content-Type"].startswith("text/html")


def test_join_page_renders_for_an_existing_room(client):
    room_id = make_room(client)
    response = client.get(f"/room/{room_id}/join")
    assert response.status_code == 200
    assert room_id.encode() in response.data


def test_join_page_for_an_unknown_room_is_a_404(client):
    assert client.get("/room/zzzzzz/join").status_code == 404


def test_table_page_renders_for_an_existing_room(client):
    room_id = make_room(client)
    assert client.get(f"/room/{room_id}").status_code == 200


def test_table_page_for_an_unknown_room_is_a_404(client):
    assert client.get("/room/zzzzzz").status_code == 404


def test_qr_endpoint_returns_svg(client):
    room_id = make_room(client)
    response = client.get(f"/room/{room_id}/qr.svg")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("image/svg+xml")
    assert b"<svg" in response.data


def test_qr_for_an_unknown_room_is_a_404(client):
    assert client.get("/room/zzzzzz/qr.svg").status_code == 404


def test_theme_css_is_served(client):
    response = client.get("/static/css/theme.css")
    assert response.status_code == 200
    assert b"--cyan" in response.data


def test_pages_reference_no_external_hosts(client):
    room_id = make_room(client)
    for path in ("/", f"/room/{room_id}/join", f"/room/{room_id}"):
        body = client.get(path).get_data(as_text=True)
        assert "http://" not in body.replace("http://www.w3.org", "")
        assert "https://" not in body
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/api/test_pages.py -v`
Expected: FAIL — `404` on `/`.

- [ ] **Step 3: Write `static/css/theme.css`**

```css
/* Critical Path — engineering review room, not a tavern. */
:root {
  --ground:   #0f141a;
  --panel:    #161d26;
  --panel-2:  #1d2632;
  --line:     #2b3644;
  --ink:      #dfe6ee;
  --ink-dim:  #8b98a8;
  --cyan:     #4fd6e8;
  --amber:    #f2b34b;
  --red:      #e5624d;
  --green:    #62c98a;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --sans: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background-color: var(--ground);
  /* faint drafting grid */
  background-image:
    linear-gradient(rgba(79,214,232,.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(79,214,232,.035) 1px, transparent 1px);
  background-size: 32px 32px;
  color: var(--ink);
  font-family: var(--sans);
  font-size: 15px;
  line-height: 1.55;
}

a { color: var(--cyan); }

header.bar {
  display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
  padding: 14px 20px; border-bottom: 1px solid var(--line);
  background: rgba(15,20,26,.9); position: sticky; top: 0; z-index: 5;
}
header.bar h1 {
  margin: 0; font-size: 16px; letter-spacing: .16em; text-transform: uppercase;
  font-weight: 600;
}
.dm-badge {
  margin-left: auto; font-family: var(--mono); font-size: 11px;
  color: var(--ink-dim); border: 1px solid var(--line);
  padding: 2px 8px; border-radius: 3px;
}

main { padding: 20px; max-width: 1400px; margin: 0 auto; }

.panel {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 6px; padding: 16px;
}
.panel h2 {
  margin: 0 0 12px; font-size: 11px; letter-spacing: .16em;
  text-transform: uppercase; color: var(--ink-dim); font-weight: 600;
}

.num { font-family: var(--mono); font-variant-numeric: tabular-nums; }

button, .btn {
  font: inherit; cursor: pointer; color: var(--ground);
  background: var(--cyan); border: 0; border-radius: 4px;
  padding: 9px 16px; font-weight: 600;
}
button:disabled { background: var(--panel-2); color: var(--ink-dim); cursor: not-allowed; }
button.ghost { background: transparent; color: var(--cyan); border: 1px solid var(--line); }

input, select {
  font: inherit; background: var(--panel-2); color: var(--ink);
  border: 1px solid var(--line); border-radius: 4px; padding: 9px 11px; width: 100%;
}

/* --- bars ---------------------------------------------------------------- */
.bar-track { height: 6px; background: var(--panel-2); border-radius: 3px; overflow: hidden; }
.bar-fill { height: 100%; transition: width .35s ease; }
.bar-fill.stamina { background: var(--green); }
.bar-fill.focus   { background: var(--cyan); }
.bar-fill.severity{ background: var(--amber); }

/* --- layout -------------------------------------------------------------- */
.table-grid { display: grid; grid-template-columns: 240px 1fr 280px; gap: 16px; align-items: start; }
@media (max-width: 1000px) { .table-grid { grid-template-columns: 1fr; } }

.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 14px; }

/* --- class / room cards -------------------------------------------------- */
.pick {
  text-align: left; background: var(--panel); color: var(--ink);
  border: 1px solid var(--line); border-radius: 6px; padding: 14px;
  display: block; width: 100%; font-weight: 400;
}
.pick:hover:not(:disabled) { border-color: var(--cyan); }
.pick:disabled { opacity: .4; }
.pick .name { font-weight: 600; display: block; margin-bottom: 2px; }
.pick .stats { font-family: var(--mono); font-size: 12px; color: var(--cyan); }
.pick .role  { font-size: 13px; color: var(--ink-dim); }

/* --- party roster -------------------------------------------------------- */
.member { padding: 10px 0; border-bottom: 1px solid var(--line); }
.member:last-child { border-bottom: 0; }
.member.active { box-shadow: inset 3px 0 0 var(--amber); padding-left: 9px; }
.member.down { opacity: .45; }
.member .who { display: flex; justify-content: space-between; gap: 8px; }

/* --- resource rail ------------------------------------------------------- */
.rail-row { display: flex; justify-content: space-between; padding: 7px 0; border-bottom: 1px dashed var(--line); }
.rail-row:last-child { border-bottom: 0; }
.rail-row .v { font-family: var(--mono); }
.debt { color: var(--red); }
.debt-meter { height: 4px; background: var(--red); border-radius: 2px; transition: width .4s ease; }

/* --- story log ----------------------------------------------------------- */
#log { max-height: 58vh; overflow-y: auto; }
.entry { padding: 12px 0; border-bottom: 1px solid var(--line); }
.entry:last-child { border-bottom: 0; }
.entry .prose { margin: 6px 0 0; }
.entry.pending .prose { color: var(--ink-dim); font-style: italic; }

.roll {
  font-family: var(--mono); font-size: 12px; color: var(--ink-dim);
  border-left: 2px solid var(--line); padding-left: 10px;
}
.roll .nat { color: var(--ink); font-weight: 600; }
.roll.crit   .nat { color: var(--green); }
.roll.fumble .nat { color: var(--red); }

@keyframes pulse-green { from { background: rgba(98,201,138,.22); } to { background: transparent; } }
@keyframes pulse-red   { from { background: rgba(229,98,77,.22);  } to { background: transparent; } }
.flash-crit   { animation: pulse-green 1.1s ease-out; }
.flash-fumble { animation: pulse-red   1.1s ease-out; }
@media (prefers-reduced-motion: reduce) {
  .flash-crit, .flash-fumble { animation: none; }
  .bar-fill, .debt-meter { transition: none; }
}

/* --- abilities ----------------------------------------------------------- */
.abilities { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; }
.ability { text-align: left; background: var(--panel-2); color: var(--ink); border: 1px solid var(--line); padding: 10px; }
.ability:not(:disabled):hover { border-color: var(--cyan); }
.ability .cost { font-family: var(--mono); font-size: 11px; color: var(--amber); }
.ability .why  { font-size: 11px; color: var(--ink-dim); display: block; margin-top: 4px; }

.phase-track { display: flex; gap: 4px; margin-top: 8px; }
.phase-track i { flex: 1; height: 4px; background: var(--panel-2); border-radius: 2px; }
.phase-track i.done { background: var(--cyan); }
.phase-track i.now  { background: var(--amber); }
```

- [ ] **Step 4: Write the templates**

`templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Critical Path{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='css/theme.css') }}">
</head>
<body>
  <header class="bar">
    <h1>Critical Path</h1>
    {% block subtitle %}{% endblock %}
    <span class="dm-badge" id="dm-badge">DM: {{ narrator_name }}</span>
  </header>
  <main>{% block content %}{% endblock %}</main>
  {% block scripts %}{% endblock %}
</body>
</html>
```

`templates/lobby.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="card-grid">
  <section class="panel">
    <h2>New programme</h2>
    <form id="create">
      <label for="name">Programme name</label>
      <input id="name" name="name" placeholder="Kestrel" required>
      <label for="archetype" style="display:block;margin-top:12px">System</label>
      <select id="archetype" name="archetype">
        {% for a in archetypes %}
        <option value="{{ a.id }}">{{ a.name }}</option>
        {% endfor %}
      </select>
      <button type="submit" style="margin-top:14px">Open the project</button>
      <p id="create-error" class="debt" role="alert"></p>
    </form>
  </section>

  <section class="panel">
    <h2>Join by code</h2>
    <form id="join-code">
      <input id="code" placeholder="room code" required>
      <button type="submit" style="margin-top:12px">Join</button>
    </form>
  </section>
</div>

<h2 style="margin-top:28px">Rooms</h2>
<div class="card-grid" id="rooms"></div>
{% endblock %}
{% block scripts %}
<script src="{{ url_for('static', filename='js/lobby.js') }}"></script>
{% endblock %}
```

`templates/join.html`:

```html
{% extends "base.html" %}
{% block subtitle %}<span class="num">{{ room.name }} · {{ room_id }}</span>{% endblock %}
{% block content %}
<div class="table-grid">
  <section class="panel">
    <h2>Party</h2>
    <div id="roster"></div>
    <p id="genesis" class="roll">The DM is drafting your programme…</p>
    <img src="{{ url_for('room_qr', room_id=room_id) }}" alt="QR code to join this room"
         width="150" height="150" style="margin-top:14px;background:#fff;padding:6px;border-radius:4px">
  </section>

  <section class="panel" style="grid-column: span 2">
    <h2>Choose your discipline</h2>
    <form id="seat">
      <input id="display-name" placeholder="Your name" required maxlength="24">
      <p id="join-error" class="debt" role="alert"></p>
      <div class="card-grid" id="classes" style="margin-top:14px"></div>
    </form>
    <button id="start" class="ghost" style="margin-top:18px">Start the programme</button>
  </section>
</div>
{% endblock %}
{% block scripts %}
<script>window.ROOM_ID = {{ room_id | tojson }};</script>
<script src="{{ url_for('static', filename='js/lobby.js') }}"></script>
{% endblock %}
```

- [ ] **Step 5: Write `static/js/lobby.js`**

```javascript
/* Lobby and character select. No framework, no build step. */
const api = async (url, options) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" }, ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `request failed (${response.status})`);
  return body;
};

const show = (id, message) => {
  const node = document.getElementById(id);
  if (node) node.textContent = message || "";
};

/* --- lobby ---------------------------------------------------------------- */
const createForm = document.getElementById("create");
if (createForm) {
  createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    show("create-error", "");
    try {
      const { room_id } = await api("/api/rooms", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("name").value,
          archetype: document.getElementById("archetype").value,
        }),
      });
      location.href = `/room/${room_id}/join`;
    } catch (error) { show("create-error", error.message); }
  });

  document.getElementById("join-code").addEventListener("submit", (event) => {
    event.preventDefault();
    const code = document.getElementById("code").value.trim();
    if (code) location.href = `/room/${code}/join`;
  });

  api("/api/rooms").then(({ rooms }) => {
    const target = document.getElementById("rooms");
    if (!rooms.length) { target.innerHTML = "<p class='roll'>No rooms yet.</p>"; return; }
    target.innerHTML = rooms.map((room) => `
      <a class="pick" href="/room/${room.room_id}/join">
        <span class="name">${room.name}</span>
        <span class="role">${room.archetype.replace(/_/g, " ")} ·
          phase ${room.phase_index + 1} of 5 · ${room.player_count} seated</span>
        <span class="stats">${room.room_id}</span>
      </a>`).join("");
  });
}

/* --- character select ------------------------------------------------------ */
const seatForm = document.getElementById("seat");
if (seatForm) {
  const roomId = window.ROOM_ID;
  let taken = new Set();

  const refresh = async () => {
    const state = await api(`/api/rooms/${roomId}/state`);
    taken = new Set(Object.values(state.characters).map((c) => c.class_id));
    document.getElementById("roster").innerHTML =
      Object.values(state.characters).map((c) => `
        <div class="member"><div class="who">
          <span>${c.name}</span><span class="stats">${c.class_id.replace(/_/g, " ")}</span>
        </div></div>`).join("") || "<p class='roll'>Nobody seated yet.</p>";
    if (state.room.premise) show("genesis", state.room.premise);
    if (state.you) location.href = `/room/${roomId}`;
    if (state.room.status === "active") location.href = `/room/${roomId}`;
    return state;
  };

  const drawClasses = ({ classes }) => {
    document.getElementById("classes").innerHTML = classes.map((cls) => `
      <button type="button" class="pick" data-class="${cls.id}"
              ${taken.has(cls.id) ? "disabled" : ""}>
        <span class="name">${cls.name}</span>
        <span class="stats">${cls.primary} / ${cls.secondary}</span>
        <span class="role">${cls.role}</span>
        <span class="role" style="margin-top:6px;display:block">${cls.blurb}</span>
      </button>`).join("");

    document.getElementById("classes").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-class]");
      if (!button || button.disabled) return;
      const name = document.getElementById("display-name").value.trim();
      if (!name) { show("join-error", "Enter your name first."); return; }
      try {
        await api(`/api/rooms/${roomId}/join`, {
          method: "POST",
          body: JSON.stringify({ display_name: name, class_id: button.dataset.class }),
        });
        location.href = `/room/${roomId}`;
      } catch (error) { show("join-error", error.message); }
    });
  };

  document.getElementById("start").addEventListener("click", async () => {
    try {
      await api(`/api/rooms/${roomId}/start`, { method: "POST" });
      location.href = `/room/${roomId}`;
    } catch (error) { show("join-error", error.message); }
  });

  refresh().then(() => api("/api/classes")).then(drawClasses);
  setInterval(refresh, 3000);
}
```

- [ ] **Step 6: Add the HTML routes to `app.py`**

Add `from flask import abort, url_for` and `import io`, `import segno` to the imports,
then inside `create_app`, before `return app`:

```python
    # --- pages -------------------------------------------------------------

    def _room_or_404(room_id):
        try:
            return app.service.snapshot(room_id)
        except ServiceError:
            abort(404)

    @app.get("/")
    def page_lobby():
        return render_template("lobby.html", archetypes=app.archetypes,
                               narrator_name=app.config.get("NARRATOR_NAME",
                                                            "template"))

    @app.get("/room/<room_id>/join")
    def page_join(room_id):
        state = _room_or_404(room_id)
        return render_template("join.html", room_id=room_id, room=state["room"],
                               narrator_name=app.config.get("NARRATOR_NAME",
                                                            "template"))

    @app.get("/room/<room_id>")
    def page_table(room_id):
        state = _room_or_404(room_id)
        return render_template("table.html", room_id=room_id, room=state["room"],
                               narrator_name=app.config.get("NARRATOR_NAME",
                                                            "template"))

    @app.get("/room/<room_id>/qr.svg")
    def room_qr(room_id):
        _room_or_404(room_id)
        join_url = url_for("page_join", room_id=room_id, _external=True)
        buffer = io.BytesIO()
        segno.make(join_url, error="m").save(buffer, kind="svg", scale=4,
                                             dark="#0f141a", light="#ffffff")
        return Response(buffer.getvalue(), mimetype="image/svg+xml")
```

`table.html` arrives in Task 19; until then `/room/<id>` raises a
`TemplateNotFound`. Create a one-line placeholder now so Task 18's tests pass:
`echo '{% extends "base.html" %}{% block content %}<p>Loading…</p>{% endblock %}' > templates/table.html`.

- [ ] **Step 7: Run to verify they pass**

Run: `pytest tests/api/test_pages.py -v`
Expected: PASS — 10 tests.

- [ ] **Step 8: Commit**

```bash
git add static/ templates/ app.py tests/api/test_pages.py
git commit -m "feat: engineering-console theme, lobby, and character select"
```

---

## Task 19: The table

**Files:**
- Create: `templates/table.html` (replacing the placeholder), `static/js/game.js`
- Test: `tests/api/test_table_page.py`

**Interfaces:**
- Consumes: `GET /api/rooms/<id>/state`, `POST .../action`, `POST .../end-turn`,
  `GET .../stream`.
- Produces: the playable three-column table described in spec §6.2.

- [ ] **Step 1: Write the failing tests**

```python
# tests/api/test_table_page.py
from tests.api.conftest import make_room


def seat(client, room_id, name="Ada", class_id="computer_scientist"):
    return client.post(f"/api/rooms/{room_id}/join",
                       json={"display_name": name, "class_id": class_id})


def test_table_has_the_three_regions(client):
    room_id = make_room(client)
    body = client.get(f"/room/{room_id}").get_data(as_text=True)
    for anchor in ('id="party"', 'id="log"', 'id="rail"', 'id="hazard"',
                   'id="abilities"'):
        assert anchor in body


def test_table_loads_the_game_script(client):
    room_id = make_room(client)
    assert "js/game.js" in client.get(f"/room/{room_id}").get_data(as_text=True)


def test_table_exposes_the_room_id_to_javascript(client):
    room_id = make_room(client)
    body = client.get(f"/room/{room_id}").get_data(as_text=True)
    assert "window.ROOM_ID" in body and room_id in body


def test_game_script_is_served(client):
    response = client.get("/static/js/game.js")
    assert response.status_code == 200
    assert b"EventSource" in response.data


def test_game_script_reconnects_with_last_event_id(client):
    body = client.get("/static/js/game.js").get_data(as_text=True)
    assert "since=" in body


def test_table_shows_the_narrator_badge(client):
    room_id = make_room(client)
    assert 'id="dm-badge"' in client.get(f"/room/{room_id}").get_data(as_text=True)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/api/test_table_page.py -v`
Expected: FAIL — the placeholder `table.html` has none of the anchors.

- [ ] **Step 3: Write `templates/table.html`**

```html
{% extends "base.html" %}
{% block subtitle %}
<span class="num" id="room-label">{{ room.name }} · {{ room_id }}</span>
<span class="num" id="phase-label"></span>
{% endblock %}
{% block content %}
<div class="table-grid">

  <section class="panel" id="party-panel">
    <h2>Party</h2>
    <div id="party"></div>
  </section>

  <section>
    <div class="panel">
      <h2>Programme</h2>
      <p id="premise" class="roll">The DM is drafting your programme…</p>
      <div id="log" style="margin-top:12px"></div>
    </div>

    <div class="panel" style="margin-top:16px">
      <h2>Your move <span id="turn-hint" class="roll"></span></h2>
      <div class="abilities" id="abilities"></div>
      <button id="pass" class="ghost" style="margin-top:12px">Pass the turn</button>
      <p id="action-error" class="debt" role="alert"></p>
    </div>
  </section>

  <section>
    <div class="panel" id="hazard">
      <h2>Open problem</h2>
      <div id="hazard-body"><p class="roll">None active.</p></div>
    </div>

    <div class="panel" id="rail" style="margin-top:16px">
      <h2>Programme health</h2>
      <div id="rail-body"></div>
      <div class="phase-track" id="phase-track"></div>
    </div>
  </section>

</div>
{% endblock %}
{% block scripts %}
<script>window.ROOM_ID = {{ room_id | tojson }};</script>
<script src="{{ url_for('static', filename='js/game.js') }}"></script>
{% endblock %}
```

- [ ] **Step 4: Write `static/js/game.js`**

```javascript
/* The table. One EventSource, one state snapshot, plain DOM updates. */
const ROOM = window.ROOM_ID;
let lastSeq = 0;
let me = null;

const el = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

const api = async (url, options) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" }, ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `request failed (${response.status})`);
  return body;
};

const bar = (value, max, kind) => `
  <div class="bar-track"><div class="bar-fill ${kind}"
    style="width:${max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0}%"></div></div>`;

/* --- rendering ------------------------------------------------------------ */

function renderParty(state) {
  el("party").innerHTML = Object.values(state.characters).map((c) => {
    const active = state.turn.active_player_id === c.player_id;
    const down = c.stamina <= 0;
    return `<div class="member ${active ? "active" : ""} ${down ? "down" : ""}">
      <div class="who">
        <span>${esc(c.name)}${down ? " · burned out" : ""}</span>
        <span class="num">L${c.level}</span>
      </div>
      <div class="role">${esc(c.class_id.replace(/_/g, " "))}</div>
      <div class="num" style="font-size:12px">
        ${c.stamina}/${c.max_stamina} stamina</div>
      ${bar(c.stamina, c.max_stamina, "stamina")}
      <div class="num" style="font-size:12px">${c.focus}/${c.max_focus} focus</div>
      ${bar(c.focus, c.max_focus, "focus")}
    </div>`;
  }).join("");
}

function renderHazard(state) {
  const h = state.hazard;
  if (!h) { el("hazard-body").innerHTML = "<p class='roll'>None active.</p>"; return; }
  el("hazard-body").innerHTML = `
    <p style="font-weight:600;margin:0">${esc(h.name)}${h.is_boss ? " ⚑" : ""}</p>
    <p class="role" style="margin:4px 0 10px">${esc(h.description)}</p>
    <div class="num" style="font-size:12px">${h.severity}/${h.max_severity} severity</div>
    ${bar(h.severity, h.max_severity, "severity")}
    <div class="roll" style="margin-top:10px">
      weakness: ${h.weakness ? esc(h.weakness) : "unknown"}<br>
      difficulty: ${h.dc ?? "unknown"}<br>
      next: ${h.attack_type ? esc(h.attack_type.replace(/_/g, " ")) : "unknown"}
    </div>`;
}

function renderRail(state) {
  const p = state.party;
  el("rail-body").innerHTML = `
    <div class="rail-row"><span>Budget</span><span class="v">${p.budget}</span></div>
    <div class="rail-row"><span>Schedule</span><span class="v">${p.schedule} d</span></div>
    <div class="rail-row"><span class="debt">Technical Debt</span>
      <span class="v debt">${p.tech_debt}</span></div>
    <div class="debt-meter" style="width:${Math.min(100, p.tech_debt * 2)}%"></div>
    <p class="roll" style="margin-top:8px">
      DC penalty: +${Math.floor(p.tech_debt / 10)}</p>`;

  el("phase-track").innerHTML = Array.from({ length: state.phase.count }, (_, i) =>
    `<i class="${i < state.phase.index ? "done" : i === state.phase.index ? "now" : ""}"></i>`
  ).join("");
  el("phase-label").textContent =
    `${state.phase.name} · ${state.phase.index + 1}/${state.phase.count}`;
}

function renderAbilities(state) {
  me = state.you;
  const mine = state.turn.active_player_id === me?.player_id;
  el("turn-hint").textContent = me
    ? (mine ? "it is your turn" : "waiting for another engineer")
    : "you are watching";
  el("pass").disabled = !mine;
  if (!me) { el("abilities").innerHTML = ""; return; }

  el("abilities").innerHTML = me.abilities.map((a) => {
    let why = "";
    if (!mine) why = "not your turn";
    else if (me.focus < a.focus_cost) why = `needs ${a.focus_cost} focus, you have ${me.focus}`;
    else if (me.stamina <= 0) why = "you are burned out";
    return `<button class="ability" data-ability="${a.id}" ${why ? "disabled" : ""}>
      <span style="font-weight:600">${esc(a.name)}</span>
      <span class="cost"> ${a.focus_cost}F · ${esc(a.stat)}</span>
      <span class="why">${esc(why || a.flavor)}</span>
    </button>`;
  }).join("");
}

/* --- the log -------------------------------------------------------------- */

function entryFor(seq) {
  let node = el(`e${seq}`);
  if (!node) {
    node = document.createElement("div");
    node.className = "entry pending";
    node.id = `e${seq}`;
    el("log").append(node);
    el("log").scrollTop = el("log").scrollHeight;
  }
  return node;
}

function renderEvent(event) {
  if (event.seq > lastSeq) lastSeq = event.seq;

  if (event.kind === "premise") { el("premise").textContent = event.premise; return; }

  if (event.kind === "narration") {
    const node = entryFor(event.event_seq ?? event.seq);
    node.classList.remove("pending");
    let prose = node.querySelector(".prose");
    if (!prose) { prose = document.createElement("p"); prose.className = "prose"; node.append(prose); }
    prose.textContent = event.text;
    el("log").scrollTop = el("log").scrollHeight;
    return;
  }

  if (event.kind === "action") {
    const node = entryFor(event.seq);
    const sign = event.total >= event.dc ? "≥" : "<";
    node.insertAdjacentHTML("afterbegin", `
      <div class="roll ${event.outcome}">
        <span class="nat">${event.natural}</span>
        ${event.stat_mod >= 0 ? "+" : ""}${event.stat_mod}
        ${event.roll_bonus ? `+${event.roll_bonus}` : ""}
        = ${event.total} ${sign} DC ${event.dc}
        · ${esc(event.ability_name)} · ${esc(event.outcome)}
        ${event.rerolled ? " · rerolled" : ""}
      </div>`);
    if (event.outcome === "crit") node.classList.add("flash-crit");
    if (event.outcome === "fumble") node.classList.add("flash-fumble");
    return;
  }

  const labels = {
    hazard_attack: "The problem bites back.",
    hazard_defeated: "Problem closed.",
    phase_advanced: "Phase gate cleared.",
    game_over: "The programme has ended.",
    player_joined: "A new engineer joins.",
    game_started: "The programme begins.",
    campaign_updated: "The plan is revised.",
    passed: "Turn passed.",
  };
  if (labels[event.kind]) {
    const node = entryFor(event.seq);
    node.classList.remove("pending");
    node.insertAdjacentHTML("beforeend",
      `<div class="roll">${labels[event.kind]}</div>`);
  }
}

/* --- wiring --------------------------------------------------------------- */

async function refresh() {
  const state = await api(`/api/rooms/${ROOM}/state`);
  renderParty(state); renderHazard(state); renderRail(state); renderAbilities(state);
  el("dm-badge").textContent = `DM: ${state.narrator ?? "template"}`;
  if (state.room.premise) el("premise").textContent = state.room.premise;
  return state;
}

function connect() {
  const source = new EventSource(`/api/rooms/${ROOM}/stream?since=${lastSeq}`);
  source.onmessage = () => {};
  ["action", "narration", "premise", "hazard_attack", "hazard_defeated",
   "phase_advanced", "game_over", "player_joined", "game_started",
   "campaign_updated", "passed", "room_created"].forEach((kind) => {
    source.addEventListener(kind, (message) => {
      renderEvent(JSON.parse(message.data));
      refresh();
    });
  });
  source.onerror = () => {
    source.close();
    setTimeout(connect, 2000);       // reconnect resumes from lastSeq
  };
}

el("abilities").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-ability]");
  if (!button || button.disabled) return;
  el("action-error").textContent = "";
  try {
    await api(`/api/rooms/${ROOM}/action`, {
      method: "POST",
      body: JSON.stringify({ ability_id: button.dataset.ability }),
    });
  } catch (error) { el("action-error").textContent = error.message; }
});

el("pass").addEventListener("click", async () => {
  el("action-error").textContent = "";
  try { await api(`/api/rooms/${ROOM}/end-turn`, { method: "POST" }); }
  catch (error) { el("action-error").textContent = error.message; }
});

refresh().then(connect);
```

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/api/ -v`
Expected: PASS — 6 table tests plus everything earlier.

- [ ] **Step 6: Play it**

Run: `make run`, open `http://localhost:5000` in two browser windows, create a room,
seat two classes, start, and take a few turns. Confirm: rolls appear instantly, prose
arrives a beat later, both windows update without a refresh.

- [ ] **Step 7: Commit**

```bash
git add templates/table.html static/js/game.js tests/api/test_table_page.py
git commit -m "feat: the table - party panel, story log, ability bar, resource rail"
```

---

## Task 20: Concurrency proof, documentation, and the end-to-end run

**Files:**
- Create: `tests/test_concurrency.py`, `tests/test_end_to_end.py`, `README.md`
- Modify: `Makefile`

**Interfaces:**
- Consumes: everything.
- Produces: no new interfaces — this task proves the system holds together and
  documents how to run it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_concurrency.py
"""The per-room lock must make simultaneous actions safe."""
import threading
import pytest
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from engine.rules import RuleError
from service import GameService, ServiceError


@pytest.fixture
def table(tmp_path):
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes())
    room_id = svc.create_room("Kestrel", "aircraft")
    a = svc.join_room(room_id, "Ada", "computer_scientist")
    b = svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    return svc, room_id, a["player_id"], b["player_id"]


def test_simultaneous_actions_produce_exactly_one_success(table):
    svc, room_id, ada, ben = table
    results, errors = [], []
    barrier = threading.Barrier(8)

    def attempt(player_id, ability_id):
        barrier.wait()
        try:
            results.append(svc.act(room_id, player_id, ability_id))
        except (RuleError, ServiceError) as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=attempt, args=(ada, "unit_test_barrage"))
               for _ in range(4)]
    threads += [threading.Thread(target=attempt, args=(ben, "shop_floor_fix"))
                for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)

    assert len(results) == 1, f"{len(results)} actions resolved; expected 1"
    assert len(errors) == 7


def test_focus_is_never_double_spent(table):
    svc, room_id, ada, _ = table
    before = svc.snapshot(room_id)["characters"][ada]["focus"]
    barrier = threading.Barrier(5)

    def attempt():
        barrier.wait()
        try:
            svc.act(room_id, ada, "unit_test_barrage")
        except (RuleError, ServiceError):
            pass

    threads = [threading.Thread(target=attempt) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)

    after = svc.snapshot(room_id)["characters"][ada]["focus"]
    assert after == before - 1


def test_event_sequence_numbers_never_collide(table):
    svc, room_id, ada, ben = table
    for _ in range(6):
        state = svc.snapshot(room_id)
        active = state["turn"]["order"][state["turn"]["turn_index"]]
        svc.end_turn(room_id, active)
    seqs = [e["seq"] for e in svc.events_since(room_id, 0)]
    assert len(seqs) == len(set(seqs))
    assert seqs == sorted(seqs)


def test_two_rooms_do_not_block_each_other(tmp_path):
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes())
    rooms = []
    for index in range(2):
        room_id = svc.create_room(f"R{index}", "car")
        player = svc.join_room(room_id, f"P{index}", "computer_scientist")
        svc.start_game(room_id)
        rooms.append((room_id, player["player_id"]))

    done = []

    def play(room_id, player_id):
        for _ in range(3):
            svc.end_turn(room_id, player_id)
        done.append(room_id)

    threads = [threading.Thread(target=play, args=pair) for pair in rooms]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)
    assert len(done) == 2
```

```python
# tests/test_end_to_end.py
"""A full game, driven through the HTTP API, with a deterministic narrator."""
import pytest
from app import create_app
from narrator.base import FakeNarrator
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker


@pytest.fixture
def rig(tmp_path):
    queue = NarrationQueue()
    app = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                      "TESTING": True, "NARRATION": queue})
    worker = NarrationWorker(queue, FakeNarrator("The bracket holds."),
                             app.service, app.broker)
    return app, worker


def test_a_room_can_be_created_joined_started_and_played(rig):
    app, worker = rig
    ada, ben = app.test_client(), app.test_client()

    room_id = ada.post("/api/rooms", json={"name": "Kestrel",
                                           "archetype": "aircraft"}).get_json()["room_id"]
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ben.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ben", "class_id": "mechanical_technician"})
    ada.post(f"/api/rooms/{room_id}/start")

    for _ in range(20):
        state = ada.get(f"/api/rooms/{room_id}/state").get_json()
        if state["room"]["status"] != "active":
            break
        active = state["turn"]["active_player_id"]
        client = ada if active == state["characters"][active]["player_id"] and \
            state["you"] and state["you"]["player_id"] == active else ben
        response = client.post(f"/api/rooms/{room_id}/action",
                               json={"ability_id": "unit_test_barrage"})
        if response.status_code != 200:
            client.post(f"/api/rooms/{room_id}/end-turn")

    events = ada.get(f"/api/rooms/{room_id}/stream?once=1&since=0").get_data(as_text=True)
    assert "event: action" in events


def test_narration_reaches_the_event_log(rig):
    app, worker = rig
    ada = app.test_client()
    room_id = ada.post("/api/rooms", json={"name": "Kestrel",
                                           "archetype": "aircraft"}).get_json()["room_id"]
    ada.post(f"/api/rooms/{room_id}/join",
             json={"display_name": "Ada", "class_id": "computer_scientist"})
    ada.post(f"/api/rooms/{room_id}/start")
    ada.post(f"/api/rooms/{room_id}/action", json={"ability_id": "unit_test_barrage"})

    while worker.run_once(timeout=0.05):
        pass
    kinds = [e["kind"] for e in app.service.events_since(room_id, 0)]
    assert "narration" in kinds


def test_game_survives_a_process_restart(tmp_path):
    first = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                        "TESTING": True, "NARRATION": None})
    client = first.test_client()
    room_id = client.post("/api/rooms", json={"name": "Kestrel",
                                              "archetype": "aircraft"}).get_json()["room_id"]
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    client.post(f"/api/rooms/{room_id}/start")
    client.post(f"/api/rooms/{room_id}/action", json={"ability_id": "unit_test_barrage"})
    focus = client.get(f"/api/rooms/{room_id}/state").get_json()["you"]["focus"]

    second = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                         "TESTING": True, "NARRATION": None})
    state = second.test_client().get(f"/api/rooms/{room_id}/state").get_json()
    assert state["characters"][list(state["characters"])[0]]["focus"] == focus
    assert state["room"]["status"] == "active"


def test_lobby_lists_a_room_created_by_an_earlier_process(tmp_path):
    first = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                        "TESTING": True, "NARRATION": None})
    room_id = first.test_client().post(
        "/api/rooms", json={"name": "Kestrel",
                            "archetype": "aircraft"}).get_json()["room_id"]
    second = create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "k",
                         "TESTING": True, "NARRATION": None})
    rooms = second.test_client().get("/api/rooms").get_json()["rooms"]
    assert room_id in {r["room_id"] for r in rooms}
```

- [ ] **Step 2: Run to verify they fail or reveal real defects**

Run: `pytest tests/test_concurrency.py tests/test_end_to_end.py -v`
Expected: these may pass immediately if the locking from Task 10 is correct. If any
fail, that is a genuine defect — fix the lock or the ordering, not the test.

- [ ] **Step 3: Write `README.md`**

```markdown
# Critical Path

A LAN-multiplayer tabletop RPG where the party designs a complex engineering system
instead of raiding a dungeon. Nine engineering classes, d20 mechanics, five project
phases, and a locally-served Llama 3.2 1B model as the narrator.

## Quick start

    make install          # Flask, segno, huggingface_hub, pytest
    make run              # http://localhost:5000

Open the lobby, create a programme, pick a system (car, aircraft, weapon platform,
spacecraft, ...), and share the room code or QR with everyone on the LAN. Each player
picks one of the nine disciplines, then you take turns.

## Optional: the local LLM narrator

The game is fully playable without it — you get terser, template-generated prose and a
`DM: template` badge. To upgrade the writing:

    make install-llm      # llama-cpp-python (compiles; takes a few minutes)
    make setup            # downloads ~0.8 GB of GGUF into models/

Meta's `meta-llama/Llama-3.2-1B-Instruct` repo is gated. By default the script pulls the
identical weights from the ungated mirror `bartowski/Llama-3.2-1B-Instruct-GGUF`. To use
the official repo instead, accept the licence on Hugging Face and export `HF_TOKEN`.

On a 2-core CPU expect roughly 5-15 tokens/sec. That is fine: rules resolve in
milliseconds and prose catches up asynchronously, so nobody waits on the model.

## How it plays

- Six stats: RIGOR, INTUITION, CRAFT, SYSTEMS, COMMS, GRIT.
- One mechanic: `d20 + stat modifier + bonuses` vs the problem's difficulty.
- Spend **Focus** to use abilities; take **Stress** damage to your **Stamina**.
- The party shares **Budget**, **Schedule**, and **Technical Debt**. Every 10 points of
  Technical Debt makes everything one point harder. Cheap fixes add it.
- Five phases: Requirements, Design, Prototype, Integration, Qualification. Each ends
  with a review gate. Clear Qualification to win; run out of Budget or Schedule, or burn
  the whole team out, and you lose.

## Architecture

    engine/     pure rules: no Flask, no SQLite, no LLM (enforced by a test)
    storage/    one SQLite database per room + a rebuildable lobby index
    service.py  per-room locks; commits state before queueing any narration
    narrator/   priority queue, one worker thread, model binding, template fallback
    app.py      Flask routes, JSON API, SSE stream
    static/ templates/   vanilla HTML/CSS/JS, no build step

Rooms persist in `rooms/<code>/game.db`. The `events` table is append-only and doubles
as the SSE stream, so reconnecting, joining late, and resuming a week-old game are all
the same operation: replay from a sequence number.

## Tests

    make test

The suite never invokes the real model, and passes with `llama-cpp-python` absent.
```

- [ ] **Step 4: Extend the `Makefile`**

```makefile
.PHONY: install install-llm setup test lint run clean
install:        ; pip install -r requirements.txt
install-llm:    ; pip install -r requirements-llm.txt
setup:          ; python scripts/setup_model.py
test:           ; pytest
run:            ; python app.py
clean:          ; rm -rf .pytest_cache **/__pycache__
```

- [ ] **Step 5: Run the whole suite and the real server**

Run: `pytest -v`
Expected: PASS — every test, with `llama-cpp-python` not installed.

Run: `python -c "import llama_cpp" ; echo "(absence is fine)"` then `make run` and play
a full phase with two browser windows.

- [ ] **Step 6: Commit**

```bash
git add README.md Makefile tests/test_concurrency.py tests/test_end_to_end.py
git commit -m "test: concurrency and end-to-end coverage; docs: README"
```

---

## Task 21: Token-by-token narration streaming

**Files:**
- Modify: `narrator/worker.py`, `narrator/llm.py`, `static/js/game.js`
- Test: `tests/narrator/test_streaming.py`

**Why this task exists:** spec §5.3 requires turn narration to stream "token-by-token
over SSE so prose appears as it is written rather than after a silent pause". Tasks 14–20
publish only the finished paragraph. On a 2-core CPU that is a 10–30 second silence,
which is exactly the experience the decoupled design was meant to avoid.

**Interfaces:**
- Consumes: `NarrationWorker`, `EventBroker`, `filters.clean`.
- Produces:
  - `LlamaNarrator.stream(job) -> Iterator[str]` — yields text deltas.
  - `NarrationWorker._generate_streaming(job) -> tuple[str, str]` — used when the
    narrator exposes `stream`; publishes `narration_chunk` broker events as tokens
    arrive, then returns the full text for filtering.
  - A new broker event kind `narration_chunk` with payload
    `{"event_seq", "delta"}`. **Chunks are never written to the `events` table** — only
    the final filtered `narration` event is. A client that reconnects mid-generation
    therefore misses the typing animation and receives the finished paragraph, which is
    the correct trade: the durable log stays clean and replay stays exact.

- [ ] **Step 1: Write the failing tests**

```python
# tests/narrator/test_streaming.py
import pytest
from broker import EventBroker
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from narrator.queue_ import NarrationQueue
from narrator.worker import NarrationWorker
from service import GameService
from tests.narrator.test_worker import make_job


class StreamingNarrator:
    name = "stream"

    def __init__(self, chunks):
        self.chunks = chunks

    def narrate(self, job):
        return "".join(self.chunks)

    def stream(self, job):
        yield from self.chunks


class NoStream:
    """A narrator with no stream(), proving the non-streaming path still works."""

    name = "nostream"

    def narrate(self, job):
        return "It holds."


class BrokenStreamNarrator:
    name = "broken"

    def narrate(self, job):
        return "Fallback prose from narrate."

    def stream(self, job):
        yield "The bracket "
        raise RuntimeError("stream died mid-token")


@pytest.fixture
def rig(tmp_path):
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes())
    room_id = svc.create_room("Kestrel", "aircraft")
    a = svc.join_room(room_id, "Ada", "computer_scientist")
    svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    result = svc.act(room_id, a["player_id"], "unit_test_barrage")
    return svc, room_id, result["event_seq"]


def drain(sub):
    out = []
    while True:
        try:
            out.append(sub.get(timeout=0.2))
        except Exception:
            return out


def test_chunks_are_published_as_they_arrive(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["The ", "bracket ", "holds."]),
                    svc, broker).run_once(timeout=1)
    kinds = [e["kind"] for e in drain(sub)]
    assert kinds.count("narration_chunk") == 3
    assert kinds[-1] == "narration"


def test_chunk_payloads_reassemble_into_the_final_text(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["The ", "bracket ", "holds."]),
                    svc, broker).run_once(timeout=1)
    events = drain(sub)
    assembled = "".join(e["delta"] for e in events if e["kind"] == "narration_chunk")
    assert assembled == "The bracket holds."


def test_chunks_reference_their_action_event(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["Done."]), svc, broker).run_once(timeout=1)
    chunk = [e for e in drain(sub) if e["kind"] == "narration_chunk"][0]
    assert chunk["event_seq"] == seq


def test_chunks_are_not_written_to_the_durable_log(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["The ", "bracket ", "holds."]),
                    svc, EventBroker()).run_once(timeout=1)
    kinds = [e["kind"] for e in svc.events_since(room_id, 0)]
    assert "narration_chunk" not in kinds
    assert kinds.count("narration") == 1


def test_streamed_text_is_still_filtered(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, StreamingNarrator(["You rolled ", "a natural 20."]),
                    svc, EventBroker()).run_once(timeout=1)
    stored = svc.narration(room_id, seq)
    assert stored["source"] == "template"      # filter rejected everything
    assert "natural 20" not in stored["text"]


def test_a_stream_that_dies_falls_back_to_the_template(rig):
    svc, room_id, seq = rig
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, BrokenStreamNarrator(), svc,
                    EventBroker()).run_once(timeout=1)
    stored = svc.narration(room_id, seq)
    assert stored["status"] == "done" and stored["text"].strip()
    assert stored["source"] == "template"


def test_a_narrator_without_stream_still_works(rig):
    svc, room_id, seq = rig
    broker = EventBroker()
    sub = broker.subscribe(room_id)
    q = NarrationQueue()
    q.submit(make_job(room_id, seq))
    NarrationWorker(q, NoStream(), svc, broker).run_once(timeout=1)
    assert [e["kind"] for e in drain(sub)] == ["narration"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/narrator/test_streaming.py -v`
Expected: FAIL — no `narration_chunk` events are ever published.

- [ ] **Step 3: Add streaming to the worker**

In `narrator/worker.py`, add the streaming generator and prefer it in `_handle_turn`:

```python
    def _supports_streaming(self) -> bool:
        return callable(getattr(self.narrator, "stream", None))

    def _generate_streaming(self, job: dict) -> tuple:
        """Publish deltas as they arrive, then filter the assembled text."""
        room_id, event_seq = job["room_id"], job.get("event_seq")
        parts: list = []

        def consume():
            for delta in self.narrator.stream(job):
                parts.append(delta)
                self.broker.publish(room_id, {
                    "seq": event_seq, "kind": "narration_chunk",
                    "event_seq": event_seq, "delta": delta})
            return "".join(parts)

        try:
            raw = self._pool.submit(consume).result(timeout=self.deadline)
            text = clean(raw, _allowed_numbers(job))
            if text:
                return text, self.narrator.name
            log.info("streamed narration filtered to nothing; using template")
        except concurrent.futures.TimeoutError:
            log.warning("streamed narration exceeded %.0fs; using template",
                        self.deadline)
        except Exception:
            log.exception("stream raised; using template")
        return self.fallback.narrate(job), self.fallback.name
```

Then in `_handle_turn`, replace `text, source = self._generate(job)` with:

```python
        if self._supports_streaming():
            text, source = self._generate_streaming(job)
        else:
            text, source = self._generate(job)
```

The final `narration` event still carries the **filtered** text, so a client that saw
raw chunks gets corrected when the paragraph lands. That is deliberate: chunks are a
preview, the `narration` event is the record.

- [ ] **Step 4: Add streaming to `LlamaNarrator`**

Append to `narrator/llm.py`:

```python
    def stream(self, job: dict):
        """Yield text deltas. llama-cpp-python emits OpenAI-shaped chunk dicts."""
        model = self.load()
        sampling = GENESIS_SAMPLING if job.get("kind") in ("genesis", "hazards") \
            else SAMPLING
        for chunk in model.create_chat_completion(messages=build(job), stream=True,
                                                  **sampling):
            delta = chunk["choices"][0].get("delta", {}).get("content")
            if delta:
                yield delta
```

Genesis and hazard jobs do **not** stream — `_handle_premise` and `_handle_hazards` keep
calling `narrate`, because nobody is watching a premise being typed and the hazard parser
needs the whole text anyway.

- [ ] **Step 5: Render chunks in `static/js/game.js`**

Add `"narration_chunk"` to the `addEventListener` kind list, and handle it in
`renderEvent` **before** the `narration` branch:

```javascript
  if (event.kind === "narration_chunk") {
    const node = entryFor(event.event_seq);
    let prose = node.querySelector(".prose");
    if (!prose) {
      prose = document.createElement("p");
      prose.className = "prose";
      prose.dataset.streaming = "1";
      node.append(prose);
    }
    if (prose.dataset.streaming === "1") prose.textContent += event.delta;
    el("log").scrollTop = el("log").scrollHeight;
    return;                       // no refresh(): chunks change no game state
  }
```

and in the `narration` branch, clear the flag so the filtered text replaces the preview:

```javascript
    prose.dataset.streaming = "0";
    prose.textContent = event.text;
```

Also skip the `refresh()` call for `narration_chunk` in `connect()` — a chunk changes no
game state, and refreshing on every token would hammer the server:

```javascript
    source.addEventListener(kind, (message) => {
      const event = JSON.parse(message.data);
      renderEvent(event);
      if (event.kind !== "narration_chunk") refresh();
    });
```

- [ ] **Step 6: Run to verify they pass**

Run: `pytest tests/ -v`
Expected: PASS — 7 streaming tests and the full suite.

- [ ] **Step 7: See it**

With the model installed (`make install-llm && make setup`), run `make run` and take a
turn. Prose should begin appearing within a second or two and type itself out, rather
than arriving all at once after a long pause.

- [ ] **Step 8: Commit**

```bash
git add narrator/worker.py narrator/llm.py static/js/game.js tests/narrator/test_streaming.py
git commit -m "feat: token-by-token narration streaming over SSE"
```

---

## Done

The game is playable over the LAN, rooms persist across restarts, the ruleset is fully
unit-tested without a model or a server, and the narrator upgrades from templates to
Llama 3.2 1B the moment the GGUF is downloaded — with no code change and no downtime
beyond a restart. With the model present, prose types itself out as it is generated,
so a 20-second generation on two cores reads as writing rather than as waiting.
