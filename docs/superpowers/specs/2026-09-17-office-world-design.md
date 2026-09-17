# Office World — Design Spec

**Date:** 2026-09-17
**Status:** Approved by user ("yes, build it fast"); user waived spec review.

## Summary

A shared top-down 2D office for each game room, shown in a new **Office** tab beside the existing board. Every seated character (human or bot) owns a desk. Places in the office are an alternative source of turn actions, integrated with the existing turn order, DM wait gate, and bot policy.

## Decisions

| Question | Decision |
|---|---|
| Library | **Phaser 3.90.0** (last 3.x). Phaser 4.2.1 is current but younger and less documented; 3.90 chosen for reliability. |
| Offline | Phaser vendored to `static/vendor/phaser.min.js` by `setup.sh` (one-time download). Page loads it from the local server. The existing no-external-host test still guards all pages. |
| Art | Tiles and furniture drawn procedurally at runtime with Phaser Graphics / generated textures. No image files, no licence questions. A Kenney CC0 pack may replace them later. |
| Movement | Arrow keys / WASD, plus click-to-walk. Walking is free (no turn cost). |
| Sync | Server-authoritative **zone**, not pixel position. Clients POST only on zone change; server broadcasts `office_move` over SSE. Clients animate the walk locally. |

## Map

Fixed layout, one per room, sized for up to 6 desks (`MAX_SEATS`).

```
┌─────────────────────────────────────────────┐
│  LAB             │   BREAK ROOM             │
├──────── door ────┴──────── door ────────────┤
│  desk  desk  desk  desk  desk  desk         │
│                         WHITEBOARD          │
└─────────────────────────────────────────────┘
```

Zones: `lab`, `break_room`, `whiteboard`, `floor`, and `desk:<player_id>` for each seated character. Desks are assigned by seat order.

## Place actions

Each consumes the actor's turn, obeys `validate`-style checks (seat, room active, own turn, DM gate not closed), appends a normal event, and queues narration like an ability.

| Zone | Action id | Effect |
|---|---|---|
| `lab` | `bench_test` | If the active hazard's weakness is unrevealed: reveal it. Otherwise: +2 to the actor's next roll (condition `roll_bonus`, 1 round, target self). |
| `desk:<other>` | `pair_up` | +1 `roll_bonus` to both actor and desk owner for their next roll. Once per round per actor. Owner must be alive. |
| `break_room` | `coffee_break` | Restore stamina: `max(2, max_stamina // 4)`, capped at max. Limited to 2 uses per character per phase (reset on phase advance). |
| own desk | `customize` | Cosmetic; **does not** consume the turn and may be used any time the viewer is seated. |
| `whiteboard` | — | Focuses the existing party chat panel. No game effect. |

The actor must currently be in the matching zone (server checks the stored zone).

## Desk customization

Per-character dict: `desk_color`, `monitor` (`single`/`dual`/`laptop`), `plant` (`none`/`succulent`/`fern`), `mug` (`none`/`coffee`/`tea`), `poster` (`none`/`gantt`/`schematic`/`motivational`). Validated against fixed option lists server-side; unknown values fall back to defaults.

## Persistence

- `characters.office_zone TEXT NOT NULL DEFAULT 'floor'`
- `characters.desk TEXT NOT NULL DEFAULT '{}'`
- `characters.coffee_used INTEGER NOT NULL DEFAULT 0`
- Idempotent migrations following the existing `appearance`/`is_bot` pattern.

`engine/` stays pure: place effects live in a new pure module `engine/places.py`, called by `service.py`.

## API

- `POST /api/rooms/<id>/office/move {"zone"}`: seat required; validates the zone exists; broadcasts `office_move`. Does not consume a turn.
- `POST /api/rooms/<id>/office/act {"action", "target_id"?}`: performs a place action.
- `POST /api/rooms/<id>/office/desk {...}`: saves customization for the caller's own desk.
- `/api/rooms/<id>/state` gains per-character `office_zone` and `desk`, plus an `office` block listing zones and desk assignments.

## Bots

`bots.py` gains place-aware choices, chosen before walking:
- weakness unrevealed and no reveal ability affordable → walk to `lab`, `bench_test`
- an ally below half stamina and no heal ability affordable → `break_room`, `coffee_break` (if uses remain; the bot drinks, the ally is unaffected). Only if the bot itself is below half; otherwise fall through
- otherwise existing ability policy

Bots emit `office_move` before acting so humans see them walk. Bot chat lines gain place variants, rotated deterministically.

## UI

- `templates/table.html`: tabs **Board** / **Office**. Office tab hosts the Phaser canvas.
- `static/js/office.js`: scene with procedural tiles, avatars (name label; bots marked), zone detection on movement, place-action prompt when standing in an actionable zone ("Press E to run a bench test"), desk customization panel on own desk.
- Keyboard input captured only while the Office tab is focused; chat input must not move the avatar.
- Responsive: the canvas scales to container width; touch taps work as click-to-walk.
- Respects `prefers-reduced-motion` (no camera easing).
- All text from the server rendered with Phaser text objects or escaped DOM.

## Safety

Every test and live verification uses `tmp_path` or a scratch `git archive` copy. Nothing touches the repo's live `rooms/`.

## Tests

- migrations add the three columns to a pre-existing database
- move: unknown zone rejected; spectator 403; broadcasts `office_move`; costs no turn
- each place action: correct effect, wrong zone rejected, not-your-turn rejected, DM gate respected
- `pair_up` once per round; dead owner rejected
- `coffee_break` limited to 2 per phase and reset on phase advance
- desk customization validated; unknown values fall back
- bots walk and use places in the specified situations
- `engine/places.py` is pure (purity test covers it automatically)
- Phaser is served locally and no page references an external host
