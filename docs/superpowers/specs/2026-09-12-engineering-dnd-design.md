# Engineering D&D — Design Spec

**Date:** 2026-09-12
**Status:** Approved for planning
**Working title:** Critical Path

## 1. Summary

A LAN-multiplayer, turn-based tabletop RPG where the party designs a complex
engineering system instead of raiding a dungeon. Nine engineering character
classes, D&D-style d20 mechanics, and a local Llama 3.2 1B model acting purely
as narrator. Flask backend, one SQLite database per persistent room, plain
HTML/CSS/JS frontend served by Flask.

### Decisions locked during brainstorming

| Question | Decision |
|---|---|
| Play model | Multiplayer over LAN; each player on their own device |
| Turn action | Pick a class ability; server rolls d20 vs DC; LLM narrates the result |
| Campaign shape | Five project phases as levels (Requirements to Qualification) |
| Frontend | Rich themed UI, no build step |
| LLM serving | `llama-cpp-python` in-process, model downloaded to `models/` |
| Concurrency | Single threaded Flask process, one narration worker, SSE fan-out |

### Hardware constraint that shapes the design

Target machine: 2 CPU cores, 15 GB RAM, no GPU. Llama 3.2 1B Q4_K_M runs at
roughly 5-15 tokens/sec, so a narration paragraph takes 8-30 seconds. **Game
rules and narration are therefore fully decoupled.** Dice resolve and state
commits in milliseconds; prose streams in afterwards. The game is playable with
no model present at all.

## 2. Core mechanics

### 2.1 Stats

Six stats replace the D&D six. Range 8-16 at creation.
Modifier = `(stat - 10) // 2`.

| Stat | Domain |
|---|---|
| RIGOR | Analysis, math, verification |
| INTUITION | Debugging instinct, pattern recognition |
| CRAFT | Fabrication, soldering, rework, bench work |
| SYSTEMS | Architecture, interfaces, whole-system reasoning |
| COMMS | Stakeholders, documentation, persuasion |
| GRIT | Endurance, stress tolerance |

Stat generation: each class has a primary and secondary stat. Roll `4d6 drop
lowest` six times, assign the two highest to primary and secondary, remaining
four randomly. Clamp to 8-16.

### 2.2 The roll

Every action uses one mechanic:

```
d20 + stat_mod + ability_dc_mod  vs  hazard.dc + tech_debt_penalty
```

- `tech_debt_penalty = party.tech_debt // 10`
- Natural 20: critical, all numeric effects doubled
- Natural 1: fumble, `on_fail` effects apply AND the hazard immediately takes
  its attack action against the acting character
- No advantage/disadvantage in v1. Rerolls are granted by specific abilities.

### 2.3 Character resources

- **Stamina** (HP analogue): `8 + GRIT_mod + (2 * level)`. Reduced by Stress
  damage. At 0 the character is **Burned Out**: skips turns, cannot act, until
  an ally restores Stamina above 0.
- **Focus** (spell-slot analogue): `4 + max(RIGOR_mod, SYSTEMS_mod)`. Abilities
  cost 0-3. Regenerates +1 at the start of your turn, fully on phase transition.

### 2.4 Party resources (shared, these are the stakes)

- **Budget**: starts 100. Spent by respins, overtime, procurement.
- **Schedule**: starts 100 days. Decrements 1 per completed round, plus extra on
  certain failures.
- **Technical Debt**: starts 0. Every full 10 points adds +1 to **all** hazard
  DCs. Cheap/fast abilities add it. This is the central risk-reward tension.

### 2.5 Hazards and encounters

A hazard is an engineering failure mode with:

- `name`, `description` (LLM-generated at genesis)
- `severity` / `max_severity` (HP analogue)
- `dc` (difficulty of acting against it)
- `attack_type`: exactly one of `stress` (damages the acting character),
  `burn_budget` (drains Budget), `burn_schedule` (drains Schedule), `debt` (adds
  Technical Debt). Fixed per hazard, never random, so players can plan.
- `weakness`: a stat that receives +2 when used against it; hidden until revealed
  by an ability such as Continuity Check or Signal Integrity Scan

The hazard acts after every full round of player turns.

### 2.6 Phases

Five phases in fixed order:

1. **Requirements**
2. **Design**
3. **Prototype**
4. **Integration**
5. **Qualification**

Each phase contains 2-3 ordinary hazards followed by a **gate boss**: Design
Review, Test Readiness Review, Qualification Test, etc. Gate bosses have higher
DC and one special rule (for example, "Descope has no effect here").

Clearing a phase grants: level up (+1 to a stat of choice, +2 max Stamina, one
new ability unlocked), full Focus restore, and +10 Budget / +10 Schedule.

### 2.7 Win and lose

- **Win:** clear the Qualification gate boss.
- **Lose:** Budget <= 0, **or** Schedule <= 0, **or** every character is
  simultaneously Burned Out.

## 3. Classes and abilities

Nine classes, four abilities each. Two unlocked at creation (`unlock_phase: 0`),
two more at later phase transitions. All defined in `data/abilities.json` and
`data/classes.json` — never in Python.

| Class | Primary/Secondary | Role |
|---|---|---|
| Mechanical Engineer | CRAFT / RIGOR | Structural damage, party shielding |
| Computer Scientist | RIGOR / INTUITION | Burst damage, debt removal |
| Electrical & Electronics Engineer | RIGOR / CRAFT | Reveal, resist, heavy respin |
| System Engineer | SYSTEMS / COMMS | Force multiplier, DC reduction |
| Product Manager | COMMS / SYSTEMS | Resource manipulation, debt-for-speed |
| Mechanical Technician | CRAFT / GRIT | Cheap, reliable sustain |
| Electrical Technician | CRAFT / GRIT | Debuff, expose weakness |
| Control Systems Engineer | RIGOR / SYSTEMS | Variance reduction |
| Mechatronics Engineer | SYSTEMS / CRAFT | Generalist, ability copying |

### 3.1 Ability roster

**Mechanical Engineer** — FEA Deep Dive (2F, RIGOR, heavy damage); Design Margin
(2F, CRAFT, party damage shield 1 round); Tolerance Stack-Up (1F, RIGOR, damage
+ reveals weakness); Thermal Sink (1F, CRAFT, absorb next stress attack).

**Computer Scientist** — Binary Search Debug (3F, INTUITION, halve remaining
severity, once per hazard); Unit Test Barrage (1F, RIGOR, small damage, cannot
fumble); Refactor (2F, RIGOR, remove 5 Technical Debt); Rubber Duck (0F, COMMS,
restore 2 Focus to an ally).

**Electrical & Electronics Engineer** — Signal Integrity Scan (0F, RIGOR, reveal
DC and weakness, +2 party rolls vs this hazard); EMI Shield (1F, CRAFT, party
resists next burn attack); Power Budget (1F, SYSTEMS, damage scaled to unspent
party Focus); Board Respin (2F, CRAFT, very heavy damage, costs 15 Budget).

**System Engineer** — Requirements Trace (2F, SYSTEMS, -3 DC for the whole party
this round); Trade Study (1F, RIGOR, an ally rerolls their next roll); ICD
Lockdown (2F, SYSTEMS, hazard cannot add Technical Debt for 2 rounds); V&V Sweep
(2F, RIGOR, damage + prevents the hazard's next attack).

**Product Manager** — Descope (1F, COMMS, -10 severity immediately, +3 Technical
Debt); Stakeholder Charm (1F, COMMS, +15 Budget on success, -5 on failure);
Roadmap Rally (2F, COMMS, +2 Focus to every party member); Reprioritize (3F,
SYSTEMS, skip this hazard entirely; it returns in the next phase at +50%
severity).

**Mechanical Technician** — Shop Floor Fix (0F, CRAFT, small damage, minimum 2
even on failure); Jig & Fixture (1F, CRAFT, +3 party rolls vs this hazard for 3
rounds); Torque to Spec (1F, GRIT, damage, doubled if hazard weakness is CRAFT);
Scavenge Parts (1F, GRIT, +10 Budget, +1 Technical Debt).

**Electrical Technician** — Continuity Check (0F, CRAFT, reveal weakness, next
ally attack +4); Solder Bodge (1F, CRAFT, solid damage, +2 Technical Debt);
Harness Rework (2F, CRAFT, damage + remove 2 Technical Debt); Instrumentation
Setup (1F, RIGOR, party crit range becomes 19-20 for 2 rounds).

**Control Systems Engineer** — Kalman Filter (2F, RIGOR, treat your d20 as a flat
11, no crit and no fumble); PID Tune (1F, RIGOR, damage, +50% if used twice in a
row on the same hazard); Stability Margin (1F, SYSTEMS, cancel the next fumble in
the party); Model-in-the-Loop (2F, SYSTEMS, damage + preview the hazard's next
attack type).

**Mechatronics Engineer** — Sensor Fusion (1F, SYSTEMS, damage using the higher
of SYSTEMS or CRAFT); Actuator Integration (2F, CRAFT, damage + restore 2 Stamina
to an ally); Rapid Prototype (1F, CRAFT, damage now, +1 Technical Debt);
Cross-Domain Hack (3F, SYSTEMS, use any ally's unlocked ability, once per phase).

### 3.2 Declarative ability schema

```json
{
  "id": "descope",
  "name": "Descope",
  "class": "product_manager",
  "focus_cost": 1,
  "stat": "COMMS",
  "dc_mod": -2,
  "unlock_phase": 0,
  "target": "hazard",
  "on_success": [
    {"damage_hazard": "10"},
    {"party": {"tech_debt": 3}}
  ],
  "on_fail": [
    {"party": {"tech_debt": 1}}
  ],
  "flavor": "You quietly move it to Phase 2."
}
```

**Effect verbs** the engine understands: `damage_hazard`, `heal_ally`,
`restore_focus`, `party` (delta map over budget/schedule/tech_debt),
`stress_self`, `apply_condition`, `reveal`, `reroll_grant`, `shield`,
`skip_hazard`. Dice expressions (`"2d6+RIGOR"`) and flat integers both allowed.

Adding a class or rebalancing an ability must never require a Python change.

## 4. Persistence

### 4.1 Layout

```
rooms/
  index.db          # registry cache, rebuildable
  <room_id>/game.db # source of truth for one room
```

`index.db` holds `room_id, name, system_archetype, created_at, last_active,
phase, status, player_count` purely so the lobby can list rooms cheaply. It is a
**cache**: `rebuild_index()` regenerates it by scanning `rooms/*/game.db`. It is
never authoritative.

### 4.2 Per-room schema (`game.db`)

| Table | Contents |
|---|---|
| `room` | one row: id, name, premise, system_archetype, phase, status, rng_seed |
| `players` | player_id, display_name, session_token, class_id, joined_at, last_seen |
| `characters` | player_id FK, stats JSON, level, stamina, max_stamina, focus, max_focus, unlocked_abilities JSON, conditions JSON |
| `party` | one row: budget, schedule, tech_debt |
| `hazards` | id, phase, ordinal, name, description, severity, max_severity, dc, attack_type, weakness, revealed, defeated, is_boss |
| `turn_state` | one row: round, turn_index, active_player_id, phase, active_hazard_id |
| `events` | seq INTEGER PRIMARY KEY AUTOINCREMENT, ts, kind, actor, payload JSON |
| `narrations` | event_seq FK, status (pending/streaming/done/failed), text, source (llm/template) |

### 4.3 The event log is the backbone

Every state change appends to `events`. The SSE stream is literally "tail
`events` from sequence N". This yields three properties without extra machinery:

- A player who reloads, or joins late, replays the full story from seq 0.
- Reconnection is `Last-Event-ID` with no custom protocol.
- A room reopened days later resumes exactly where it stopped.

### 4.4 Concurrency

- SQLite `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`.
- One connection per request thread (`flask.g`), closed on teardown.
- A per-room `threading.Lock` (keyed in a module-level dict) guards the
  read-modify-write of a turn, so two simultaneous actions cannot both spend the
  same Focus or both act as the same player.
- Rooms are fully independent; separate games never contend.

**Ordering guarantee:** the action resolves and **commits** before any narration
job is enqueued. Killing the server mid-generation loses a paragraph of prose,
never game state.

### 4.5 Sessions

No accounts. Joining sets a signed cookie (`itsdangerous`, Flask `SECRET_KEY`
persisted to `instance/secret.key` on first run) holding `(room_id, player_id,
token)`. Closing the tab and returning restores the same character.

## 5. Narrator

### 5.1 Model acquisition

`scripts/setup_model.py`:

- Downloads `Llama-3.2-1B-Instruct-Q4_K_M.gguf` (~0.81 GB) into `models/` via
  `huggingface_hub.hf_hub_download`.
- Default source is the ungated mirror `bartowski/Llama-3.2-1B-Instruct-GGUF`,
  because Meta's `meta-llama/Llama-3.2-1B-Instruct` is **gated** and requires a
  license acceptance plus `HF_TOKEN`. If `HF_TOKEN` is set, prefer the official
  repo. Same weights.
- Verifies file size and SHA, is idempotent, and reports progress.
- Invoked by `make setup`.

### 5.2 Serving

`llama-cpp-python`, loaded once at startup **inside the narration worker
thread**: `n_ctx=4096`, `n_threads=2`, `n_batch=256`. Load takes 2-4 s; resident
footprint ~1 GB.

**Exactly one generation at a time.** A single worker thread drains a priority
queue. Concurrent inference on 2 cores is slower in aggregate than serial.

### 5.3 The three jobs

| Job | Priority | Cost | When |
|---|---|---|---|
| **Genesis** | low | 60-90 s | On room creation |
| **Turn narration** | high | 8-20 s | After each committed action |
| **Phase interlude** | normal | 10-20 s | On phase transition |

**Genesis is the key scheduling trick.** On room creation the creator picks a
system archetype (car, aircraft, weapon platform, spacecraft, surgical robot,
industrial robot arm, satellite bus, submarine). The model then writes:

- the project premise: program name, customer, and one distinctive constraint
- names and descriptions for **all hazards across all five phases**, up front

This runs while players are still joining and choosing classes, so the slowest
generation is hidden entirely inside lobby time. Turn 1 starts with the campaign
already written. Genesis is preempted by any high-priority turn narration.

### 5.4 Prompt discipline

The engine has already decided everything. The model is handed committed facts
and asked only to describe them.

```
You are the Game Master of an engineering RPG. Describe what just happened in
2-4 sentences of vivid, technically literate prose.
RULES: Never invent numbers. Never contradict the stated outcome. Never decide
what happens next. Do not address the player as "you the user".

PROJECT: "Kestrel" - a 4-seat hybrid-electric trainer aircraft
PHASE: Integration
HAZARD: Harmonic Coupling in the Tail Boom (severity 18/30)
ACTOR: Priya, Control Systems Engineer
ACTION: Kalman Filter
OUTCOME: SUCCESS (rolled 11 +3 vs DC 13) - severity reduced by 7
```

Sampling: `temperature=0.8`, `max_tokens=140`, stop on `\n\n`. A post-filter
drops any sentence containing a die-roll claim or a number that contradicts the
engine's committed values.

**Nothing the model emits may change game state.** The narrator is strictly a
rendering layer over rows already written to SQLite.

### 5.5 Degradation

If the model is absent, still loading, errors, or exceeds a 45 s deadline, the
room falls back to `narrator/fallback.py`: a template narrator with several
phrasings per outcome type, seeded per event for variety without repetition.

**The game is fully playable with no model installed.** The UI shows a small
indicator reading `DM: local model` or `DM: template` so the drop in prose
quality is never mysterious.

## 6. Frontend

### 6.1 Visual direction

An engineering review room, not a tavern. Dark slate ground, blueprint-cyan and
amber accents, faint drafting-grid texture. Monospace for all numerics (rolls,
severity, budget, schedule); a clean humanist sans for prose. Technical Debt is
the single red element and grows visually as it accumulates. Dice results land
with a short sharp animation; natural 20 pulses green across the log, natural 1
pulses red. No parchment, no woodgrain, no fantasy iconography.

Vanilla HTML/CSS/JS. No build step, no npm, no framework. One `game.js` owns the
`EventSource` and DOM updates. Must work in a phone browser on the LAN.

### 6.2 Screens

**Lobby (`/`)** — persistent rooms listed with phase, party size, last played.
Create (name + system archetype) or join by code. Each room shows a QR code for
phone joins on the LAN.

**Character select (`/room/<id>/join`)** — nine class cards showing role, stat
spread, and ability list. Choosing rolls stats and seats the player. Party roster
updates live while genesis generation runs behind a progress line.

**The table (`/room/<id>`)** — three columns on desktop, stacked on mobile:

- *Left:* party panel. Per character: Stamina and Focus bars, class badge, glow
  on the active turn, Burned Out state clearly marked.
- *Centre:* story log (streaming narration interleaved with structured roll cards
  reading `d20 11 +3 vs DC 13 -> -7 severity`), and beneath it the player's own
  ability buttons. Unavailable abilities are greyed **with the reason shown**
  ("needs 2 Focus, you have 1").
- *Right:* active hazard card with severity bar and revealed weakness, plus the
  party resource rail: Budget, Schedule, Technical Debt, phase progress.

### 6.3 API

| Method | Route | Behaviour |
|---|---|---|
| GET | `/api/rooms` | List rooms from `index.db` |
| POST | `/api/rooms` | Create room, init `game.db`, enqueue genesis |
| POST | `/api/rooms/<id>/join` | Claim a class, roll stats, set session cookie |
| GET | `/api/rooms/<id>/state` | Full snapshot for initial paint and reconnect |
| POST | `/api/rooms/<id>/action` | `{ability_id, target}` -> resolve, commit, enqueue narration |
| POST | `/api/rooms/<id>/end-turn` | Pass turn |
| GET | `/api/rooms/<id>/stream` | SSE event tail, honours `Last-Event-ID` |

Action resolution is synchronous and single-digit milliseconds: validate turn
ownership, validate Focus and unlock, roll, apply effects, append events, commit,
return outcome. Narration is enqueued only after the commit. **Turn order never
blocks on the model.**

## 7. Code layout

```
app.py                  # Flask app factory, routes, SSE endpoint
engine/                 # pure game logic: no Flask, no LLM, no I/O
  rules.py              # roll resolution, DC computation, win/lose checks
  dice.py               # injected seeded RNG
  effects.py            # effect-verb interpreter
  phases.py             # phase transitions, level up
  classes.py            # class/ability loading and validation
data/
  classes.json  abilities.json  hazard_templates.json  archetypes.json
storage/
  room_db.py  index_db.py  schema.sql  migrations/
narrator/
  llm.py  worker.py  prompts.py  fallback.py  queue.py
static/  templates/
scripts/setup_model.py
tests/
Makefile  requirements.txt  README.md
```

`engine/` is pure functions over state dicts with an injected RNG. That is what
makes the ruleset testable with no server, no database, and no model.

## 8. Testing strategy

1. **Engine unit tests** (the bulk). Seeded RNG. Cover all 36 abilities, every
   effect verb, DC computation including the Technical Debt escalation, crit and
   fumble handling, phase transitions, level up, and all three lose conditions
   plus the win condition.
2. **Storage tests.** Schema round-trip; simulate a mid-game kill and reopen,
   asserting state is intact; `rebuild_index()` correctness.
3. **API tests.** Flask test client with a stub narrator: join flow, turn
   ownership rejection, insufficient Focus rejection, SSE replay from a given
   `Last-Event-ID`.
4. **Concurrency test.** Fire simultaneous actions at one room from multiple
   threads and assert the per-room lock prevents double-spend.
5. **Narrator tests.** Fallback templates always produce text; the post-filter
   strips contradicting numbers; a timeout falls back rather than hanging.

The LLM is never exercised in the test suite. It is injected behind a
`Narrator` protocol with a deterministic fake.

## 9. Non-goals for v1

- Accounts, authentication, or internet exposure (LAN only, trusted network)
- Free-text player actions parsed by the LLM
- Inventory, equipment, or crafting
- Character death (Burned Out is recoverable; there is no permadeath)
- Voice, images, or map rendering
- Horizontal scaling, Redis, Celery, or any second daemon
