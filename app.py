"""Flask application: HTML pages, JSON API, and the SSE event stream."""
from __future__ import annotations

import hmac
import io
import json
import os
import queue
import secrets
import stat
import time
from pathlib import Path

import segno
from flask import (Flask, Response, abort, jsonify, render_template, request,
                   session, stream_with_context, url_for)

import appearance as appearance_lib
from bots import BotRunner
from broker import EventBroker
from engine.classes import STATS, load_catalog
from engine.phases import PHASES, load_archetypes, load_hazard_templates
from engine.rules import RuleError
from service import CHAT_MAX, MAX_SEATS, GameService, ServiceError


NAME_MAX = 40  # the client's maxlength is UX only; this is the real limit


def session_key(room_id: str) -> str:
    return f"room:{room_id}"


def _body() -> dict:
    """The request's JSON object, or {} for anything that is not one.

    `get_json(silent=True) or {}` is not enough: a valid JSON body can be a
    list, a number or a string, and every one of those reaches `.get()` and
    raises AttributeError -- a 500 for what is only an illegal request.
    """
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _text(value) -> str:
    """A body field as a stripped string. JSON fields are attacker-controlled,
    so a number or a list must not reach `.strip()`."""
    return str(value or "").strip()


def _secret_key(root: "str | Path" = "instance") -> str:
    """The key that signs the session cookie.

    That signature is the only identity check in the app, so the file is the
    whole security boundary: anyone who can read it can forge a seat for any
    room and any player. Created 0600 so a shell account on the host cannot.
    """
    path = Path(root) / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # touch first: writing then chmod-ing leaves a window in which the key
        # is on disk world-readable.
        path.touch(mode=0o600)
        path.write_text(secrets.token_urlsafe(48))
    if os.name != "nt":
        try:                                # tighten a key written by an older build
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                os.chmod(path, 0o600)
        except OSError:                     # a read-only mount is not fatal
            pass
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
        # The boss rule is never hidden. It is the one thing about a gate that
        # the party is entitled to know before they walk into it -- a rule the
        # player cannot see is just an unexplained failure.
        "rule": hazard.get("rule") or None,
    }


def _system_map(state: dict, subsystems: list) -> dict:
    """The schematic, as data: one node per subsystem with a colour for this
    phase, and nothing else.

    Deliberately no hazard names, descriptions, DCs or weaknesses -- the map is
    broadcast to spectators and to players who have revealed nothing, so it
    carries only where the work is, never what the work turns out to be. The one
    hazard anybody may read is the active one, and that already goes out through
    _public_hazard.
    """
    phase_index = state["room"]["phase_index"]
    active_id = state.get("active_hazard_id")
    here: dict = {}
    for hazard in state["hazards"]:
        if hazard["phase_index"] != phase_index:
            continue
        here.setdefault(hazard.get("subsystem") or "", []).append(hazard)

    nodes = []
    active_node = None
    for subsystem in subsystems:
        group = here.get(subsystem["id"], [])
        if not group:
            status = "clear"                     # nothing lives here this phase
        elif any(h["id"] == active_id for h in group):
            status = "active"
            active_node = subsystem["id"]
        elif all(h["defeated"] for h in group):
            status = "done"
        else:
            status = "open"
        nodes.append({
            "id": subsystem["id"], "name": subsystem["name"],
            "blurb": subsystem.get("blurb", ""), "status": status,
            "problems": len(group),
            "open": sum(1 for h in group if not h["defeated"]),
        })
    return {"nodes": nodes, "active_subsystem": active_node}


def sse_frame(seq, kind, payload) -> str:
    """One SSE frame.

    Durable events carry `id:` so a reconnecting client can resume with
    Last-Event-ID. A narration_chunk is a non-durable token preview stamped with
    the *action's* seq, which is below the latest durable sequence -- stamping it
    as an id would rewind a spec-compliant client and make it replay events it
    has already rendered. Per the SSE spec a frame with no `id:` leaves the
    client's last-event-ID untouched, which is exactly what a preview wants.
    """
    body = json.dumps({"seq": seq, "kind": kind, **payload})
    # room_closed is not in the room's log either (the log is in the trash by
    # the time it is sent), so it must not move the client's resume point.
    head = "" if kind in ("narration_chunk", "room_closed") else f"id: {seq}\n"
    return f"{head}event: {kind}\ndata: {body}\n\n"


ADMIN_ENV = "CP_ADMIN_PASSWORD"
ADMIN_FAIL_DELAY = 0.5


def admin_password() -> str:
    """The admin password, or "" when the admin pane is switched off. Read per
    request so the pane follows the environment it is actually running in."""
    return os.environ.get(ADMIN_ENV, "")


def create_app(config: "dict | None" = None) -> Flask:
    app = Flask(__name__)
    app.config.update(ROOMS_ROOT="rooms", DATA_DIR="data", NARRATION=None)
    app.config.update(config or {})
    app.secret_key = app.config.get("SECRET_KEY") or _secret_key()
    # The signed session cookie is the only identity check in the app, and
    # /end-turn and /start read no body -- without SameSite a cross-site
    # auto-submitting form could silently pass a player's turn on repeat, or
    # start a game while people are still choosing classes. Lax still sends the
    # cookie on ordinary top-level navigation, so a shared room link works.
    # Flask ships SESSION_COOKIE_SAMESITE as an explicit None, so setdefault
    # would not touch it; only override when a caller has not chosen.
    if not app.config.get("SESSION_COOKIE_SAMESITE"):
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    data_dir = app.config["DATA_DIR"]
    catalog = load_catalog(data_dir)
    app.catalog = catalog
    app.broker = EventBroker()
    app.service = GameService(app.config["ROOMS_ROOT"], catalog,
                              load_hazard_templates(data_dir),
                              load_archetypes(data_dir),
                              queue=app.config.get("NARRATION"),
                              broker=app.broker)
    app.archetypes = load_archetypes(data_dir)

    from narrator.fallback import TemplateNarrator
    from narrator.queue_ import NarrationQueue
    from narrator.worker import NarrationWorker

    app.narration_queue = app.config.get("NARRATION")
    if app.narration_queue is None and not app.config.get("TESTING"):
        app.narration_queue = NarrationQueue()
        from narrator.llm import LlamaNarrator
        model_path = app.config.get(
            "MODEL_PATH", "models/Llama-3.2-1B-Instruct-Q4_K_M.gguf")
        llama = LlamaNarrator(model_path)
        app.narrator = llama if llama.available else TemplateNarrator()
        app.config["NARRATOR_NAME"] = app.narrator.name
        app.worker = NarrationWorker(app.narration_queue, app.narrator,
                                     app.service, app.broker)
        app.worker.start()
    app.service.queue = app.narration_queue

    # Bots play themselves on their own thread, for the same reason narration
    # has one: a turn must never wait on anything that is not the player.
    # Tests drive BotRunner.run_once directly instead, so no thread is started.
    app.bots = BotRunner(app.service, catalog)
    if not app.config.get("TESTING"):
        app.bots.start()

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

    @app.get("/api/appearance-options")
    def api_appearance_options():
        return jsonify(appearance_lib.options())

    @app.get("/api/avatar.svg")
    def api_avatar():
        """Render one avatar. Every parameter is checked against the curated
        lists and anything unknown falls back, so a hand-typed URL cannot 500 or
        reach the library's enums directly."""
        chosen = appearance_lib.normalise(
            {kind: request.args.get(kind) for kind in appearance_lib.KINDS})
        return Response(
            appearance_lib.render_svg(chosen), mimetype="image/svg+xml",
            # A given combination always renders identically, so it can be
            # cached forever; the query string is the cache key.
            headers={"Cache-Control": "public, max-age=31536000"})

    # --- lobby -------------------------------------------------------------

    @app.get("/api/rooms")
    def api_rooms():
        return jsonify({"rooms": app.service.list_rooms()})

    def _create_room_from_body():
        body = _body()
        name = _text(body.get("name"))
        if not name:
            raise ServiceError("a room needs a name")
        if len(name) > NAME_MAX:
            raise ServiceError(f"a room name is at most {NAME_MAX} characters")
        return app.service.create_room(name, _text(body.get("archetype")))

    @app.post("/api/rooms")
    def api_create_room():
        return jsonify({"room_id": _create_room_from_body()}), 201

    @app.post("/api/rooms/<room_id>/join")
    def api_join(room_id):
        body = _body()
        name = _text(body.get("display_name"))
        if not name:
            raise ServiceError("you need a display name")
        if len(name) > NAME_MAX:
            raise ServiceError(f"a display name is at most {NAME_MAX} characters")
        appearance = body.get("appearance")
        joined = app.service.join_room(room_id, name,
                                       _text(body.get("class_id")),
                                       appearance if isinstance(appearance, dict)
                                       else None)
        session[session_key(room_id)] = {"player_id": joined["player_id"],
                                         "token": joined["token"]}
        session.permanent = True
        return jsonify(joined)

    @app.post("/api/rooms/<room_id>/start")
    def api_start(room_id):
        # Only someone seated at this table may start it; otherwise anyone who can
        # reach the URL could start a game while players are still picking classes.
        if not require_seat(room_id):
            return jsonify({"error": "you are not seated in this room"}), 403
        app.service.start_game(room_id)
        return jsonify({"ok": True})

    @app.post("/api/rooms/<room_id>/bots")
    def api_add_bot(room_id):
        # Same guard as /start: filling the table is a decision for the people
        # already sitting at it, not for anyone who can reach the URL.
        if not require_seat(room_id):
            return jsonify({"error": "you are not seated in this room"}), 403
        added = app.service.add_bot(room_id, _text(_body().get("class_id")))
        return jsonify(added), 201

    @app.delete("/api/rooms/<room_id>/bots/<player_id>")
    def api_remove_bot(room_id, player_id):
        if not require_seat(room_id):
            return jsonify({"error": "you are not seated in this room"}), 403
        return jsonify(app.service.remove_bot(room_id, player_id))

    @app.post("/api/rooms/<room_id>/skip-dm")
    def api_skip_dm(room_id):
        # Same guard as /start: stopping the table's wait is a decision for the
        # people sitting at it. The narration is not cancelled -- it lands later
        # and renders in its own place in the log.
        if not require_seat(room_id):
            return jsonify({"error": "you are not seated in this room"}), 403
        return jsonify(app.service.skip_dm(room_id))

    @app.post("/api/rooms/<room_id>/level-choice")
    def api_level_choice(room_id):
        found = require_seat(room_id)
        if not found:
            return jsonify({"error": "you are not seated in this room"}), 403
        body = _body()
        app.service.set_level_choice(room_id, found["player_id"],
                                     _text(body.get("stat")))
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
        bots = sum(1 for c in state["characters"].values() if c.get("is_bot"))
        return jsonify({
            "party_size": {"seated": len(state["characters"]), "bots": bots,
                           "humans": len(state["characters"]) - bots,
                           "max": MAX_SEATS},
            "room": state["room"], "party": state["party"],
            "characters": state["characters"], "hazard": _public_hazard(state),
            "map": _system_map(state, app.service.archetypes.get(
                state["room"]["archetype"], {}).get("subsystems") or []),
            "active_hazard_id": state["active_hazard_id"],
            "turn": {**state["turn"], "active_player_id": active},
            "conditions": state["conditions"],
            "phase": {"index": state["room"]["phase_index"],
                      "id": PHASES[state["room"]["phase_index"]][0],
                      "name": PHASES[state["room"]["phase_index"]][1],
                      "count": len(PHASES)},
            "you": you,
            "latest_seq": app.service._room(room_id).latest_seq(),
            "dm_wait": app.service.dm_gate(room_id),
            "narrator": app.config.get("NARRATOR_NAME", "template"),
        })

    # --- the turn ----------------------------------------------------------

    @app.post("/api/rooms/<room_id>/action")
    def api_action(room_id):
        found = require_seat(room_id)
        if not found:
            return jsonify({"error": "you are not seated in this room"}), 403
        body = _body()
        ability_id = _text(body.get("ability_id"))
        if not ability_id:
            raise ServiceError("no ability chosen")
        target_id = body.get("target_id")
        result = app.service.act(room_id, found["player_id"], ability_id,
                                 _text(target_id) or None)
        events = result.pop("events", [])
        return jsonify({"result": result, "events": events})

    @app.post("/api/rooms/<room_id>/end-turn")
    def api_end_turn(room_id):
        found = require_seat(room_id)
        if not found:
            return jsonify({"error": "you are not seated in this room"}), 403
        outcome = app.service.end_turn(room_id, found["player_id"])
        return jsonify({"result": {"passed": True}, "events": outcome["events"]})

    # --- party chat --------------------------------------------------------

    @app.post("/api/rooms/<room_id>/chat")
    def api_chat_post(room_id):
        # Talking at the table is for people at the table. A spectator can read
        # the conversation but cannot join it, same rule as /start and /bots.
        found = require_seat(room_id)
        if not found:
            return jsonify({"error": "you are not seated in this room"}), 403
        raw = _body().get("body")
        # A JSON body is attacker-controlled: a number or an object must be
        # refused outright rather than stringified into somebody's chat line.
        if not isinstance(raw, str):
            raise ServiceError("a message needs some words")
        body = raw.strip()
        if not body:
            raise ServiceError("a message needs some words")
        if len(body) > CHAT_MAX:
            raise ServiceError(f"a message is at most {CHAT_MAX} characters")
        return jsonify(app.service.post_chat(room_id, found["player_id"], body))

    @app.get("/api/rooms/<room_id>/chat")
    def api_chat_get(room_id):
        app.service.snapshot(room_id)      # unknown room -> ServiceError -> 400
        try:
            since = int(request.args.get("since") or 0)
        except (TypeError, ValueError):
            since = 0                      # a client bug, not a server error
        return jsonify({"messages": app.service.chat_since(room_id, since)})

    # --- SSE ---------------------------------------------------------------

    @app.get("/api/rooms/<room_id>/stream")
    def api_stream(room_id):
        app.service.snapshot(room_id)          # raises ServiceError -> 400 if unknown
        header = request.headers.get("Last-Event-ID")
        try:
            since = int(header or request.args.get("since") or 0)
        except (TypeError, ValueError):
            # A malformed header (or ?since=) is a client bug, not a server error:
            # fall back to ?since=, then to a full replay from zero.
            try:
                since = int(request.args.get("since") or 0)
            except (TypeError, ValueError):
                since = 0
        once = request.args.get("once") == "1"

        def generate():
            for event in app.service.events_since(room_id, since):
                yield sse_frame(event["seq"], event["kind"], event["payload"])
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
                    yield sse_frame(item["seq"], item["kind"], item)
                    if item["kind"] == "room_closed":
                        return               # nothing more will ever be written
            finally:
                app.broker.unsubscribe(room_id, q)

        return Response(stream_with_context(generate()),
                        mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

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

    # --- admin -------------------------------------------------------------
    #
    # Off unless CP_ADMIN_PASSWORD is set; when off, every admin URL is a 404 so
    # the pane does not even admit to existing. The flag lives in the same
    # signed session cookie as the seats.

    def require_admin():
        """None when the caller may proceed, else the response to return."""
        if not admin_password():
            abort(404)
        if session.get("admin") is not True:
            return jsonify({"error": "admin login required"}), 403
        return None

    @app.get("/admin")
    def page_admin():
        if not admin_password():
            abort(404)
        return render_template("admin.html", archetypes=app.archetypes,
                               is_admin=session.get("admin") is True,
                               narrator_name=app.config.get("NARRATOR_NAME",
                                                            "template"))

    @app.post("/api/admin/login")
    def api_admin_login():
        expected = admin_password()
        if not expected:
            abort(404)
        given = _body().get("password")
        given = given if isinstance(given, str) else ""
        if not hmac.compare_digest(given.encode(), expected.encode()):
            time.sleep(ADMIN_FAIL_DELAY)
            return jsonify({"error": "wrong password"}), 403
        session["admin"] = True
        return jsonify({"ok": True})

    @app.post("/api/admin/logout")
    def api_admin_logout():
        if not admin_password():
            abort(404)
        session.pop("admin", None)
        return jsonify({"ok": True})

    @app.get("/api/admin/rooms")
    def api_admin_rooms():
        denied = require_admin()
        if denied:
            return denied
        rows = []
        for room in app.service.list_rooms():
            bots = room.get("bot_count") or 0
            rows.append({
                "room_id": room["room_id"], "name": room["name"],
                "archetype": room["archetype"], "status": room["status"],
                "phase_index": room["phase_index"],
                "humans": room["player_count"] - bots, "bots": bots,
                "last_active": room["last_active"],
                "size_bytes": app.service.room_size(room["room_id"]),
            })
        return jsonify({"rooms": rows})

    @app.post("/api/admin/rooms")
    def api_admin_create_room():
        denied = require_admin()
        if denied:
            return denied
        room_id = _create_room_from_body()
        return jsonify({"room_id": room_id,
                        "join_url": url_for("page_join", room_id=room_id,
                                            _external=True)}), 201

    @app.delete("/api/admin/rooms/<room_id>")
    def api_admin_delete_room(room_id):
        denied = require_admin()
        if denied:
            return denied
        # The typed confirmation is checked here too, not only in the page: a
        # scripted or mistaken call must name the room it means to delete.
        if _text(_body().get("confirm")) != room_id:
            raise ServiceError("type the room code exactly to delete it")
        trashed = app.service.delete_room(room_id)
        return jsonify({"ok": True, "trashed_to": Path(trashed).name})

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, threaded=True)
