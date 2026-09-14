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

### Tuning inference

Four environment variables, read at startup. Anything that is not a positive
integer is ignored and the default below is used.

| Variable | Default | What it does |
| --- | --- | --- |
| `CP_MAX_TOKENS` | `60` | Longest per-turn narration, in tokens. |
| `CP_N_CTX` | `1024` | Context window. The prompts stay under ~400 tokens. |
| `CP_N_THREADS` | `2` | Inference threads. Match your core count. |
| `CP_N_BATCH` | `512` | Prompt prefill batch size. |

On CPU, `CP_MAX_TOKENS` is the one that matters: generation time is essentially
linear in the tokens emitted, so halving it roughly halves the wait.

The room-opening brief is generated once, in the background, and keeps its own
longer budget regardless of `CP_MAX_TOKENS`.

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
