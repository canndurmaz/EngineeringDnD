# Critical Path

A LAN-multiplayer tabletop RPG where the party designs a complex engineering system
instead of raiding a dungeon. Nine engineering classes, d20 mechanics, five project
phases, and a locally-served Llama 3.2 1B model as the narrator.

## Quick start

    ./setup.sh            # venv, dependencies, directories, test smoke check
    ./venv/bin/python app.py

Then open http://localhost:5000, or the LAN address the script prints at the end
so everyone else can join.

`setup.sh` is idempotent — re-run it any time. It reuses a working `./venv`
instead of rebuilding it. `./setup.sh --help` lists the flags; the useful ones
are `--with-llm` (see below) and `--skip-tests`.

Internet is needed only during setup. After that the whole thing runs offline:
inference, avatars, SQLite and the LAN server are all local.

### Everything runs through the venv

There is no global install. Every command in this README is `./venv/bin/python`,
not `python`, because the system interpreter has neither Flask nor pytest:

    ./venv/bin/python app.py
    ./venv/bin/python -m pytest

Activate the venv first (`. venv/bin/activate`) if you would rather type bare
`python` and `pytest`. The `make` targets call `./venv/bin/...` explicitly, so
they work either way.

### Manual setup

If you would rather do it by hand:

    python3 -m venv venv                            # Python 3.10+; 3.12 is what we target
    ./venv/bin/python -m pip install -r requirements.txt
    mkdir -p instance rooms                         # session key; per-room databases
    ./venv/bin/python -m pytest                     # optional smoke check
    ./venv/bin/python app.py

Open the lobby, create a programme, pick a system (car, aircraft, weapon platform,
spacecraft, ...), and share the room code or QR with everyone on the LAN. Each player
picks one of the nine disciplines, then you take turns.

## Optional: the local LLM narrator

The game is fully playable without it — you get terser, template-generated prose and a
`DM: template` badge. To upgrade the writing:

    ./setup.sh --with-llm

That does both halves: `llama-cpp-python`, then a ~0.8 GB GGUF into `models/`.
Budget 10-20 minutes. `llama-cpp-python` publishes no prebuilt wheel for common
Linux targets, so pip compiles the C++ extension from source, which needs
`build-essential`, `cmake` and `python3-dev`. The script checks for all of them
*before* starting the compile and prints the exact `apt-get` line if any are
missing.

Already ran the plain `./setup.sh` and changed your mind? `./setup.sh --llm-only`
does just the LLM half.

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

## Optional: the admin pane

Set `CP_ADMIN_PASSWORD` when starting the server to turn on `/admin`:

```bash
CP_ADMIN_PASSWORD='something long' ./venv/bin/python app.py
```

| Variable | Default | What it does |
| --- | --- | --- |
| `CP_ADMIN_PASSWORD` | unset | Enables `/admin` and `/api/admin/*`. Unset or empty, they all return 404. |

Log in at `http://<host>:5000/admin` to see every room (code, name, system,
status, humans and bots seated, last active, size on disk), open one, create one,
or delete one. The login is kept in the same signed session cookie as the seats;
changing the password does not log out an existing admin session, so rotate
`instance/secret.key` too if you need that.

Deleting asks you to type the room code. It never unlinks anything: the room's
folder is moved to `rooms/_trash/<code>-<UTC timestamp>/`, and anyone still at
that table sees "This room was closed by an admin." To restore a room, stop the
server, move the folder back to `rooms/<code>/`, and re-list it in the lobby with
`./venv/bin/python -c "from storage.index_db import IndexDB; IndexDB('rooms').rebuild()"`
(the index is only a cache over the room folders; `_trash` is skipped). Empty
`rooms/_trash/` by hand when you are sure.

The pane is plain HTTP like the rest of the app: anyone on the LAN who can sniff
traffic can read the password. Use it on a network you trust.

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

    ./venv/bin/python -m pytest      # or: make test

The suite never invokes the real model, and passes with `llama-cpp-python` absent.
