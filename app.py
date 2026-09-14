"""Flask application: HTML pages, JSON API, and the SSE event stream."""
from __future__ import annotations

import io
import json
import queue
import secrets
from pathlib import Path

import segno
from flask import (Flask, Response, abort, jsonify, render_template, request,
                   session, stream_with_context, url_for)

import appearance as appearance_lib
from broker import EventBroker
from engine.classes import STATS, load_catalog
from engine.phases import PHASES, load_archetypes, load_hazard_templates
from engine.rules import RuleError
from service import GameService, ServiceError


NAME_MAX = 40  # the client's maxlength is UX only; this is the real limit


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
    head = "" if kind == "narration_chunk" else f"id: {seq}\n"
    return f"{head}event: {kind}\ndata: {body}\n\n"


def create_app(config: "dict | None" = None) -> Flask:
    app = Flask(__name__)
    app.config.update(ROOMS_ROOT="rooms", DATA_DIR="data", NARRATION=None)
    app.config.update(config or {})
    app.secret_key = app.config.get("SECRET_KEY") or _secret_key()

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

    @app.post("/api/rooms")
    def api_create_room():
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            raise ServiceError("a room needs a name")
        if len(name) > NAME_MAX:
            raise ServiceError(f"a room name is at most {NAME_MAX} characters")
        room_id = app.service.create_room(name, body.get("archetype", ""))
        return jsonify({"room_id": room_id}), 201

    @app.post("/api/rooms/<room_id>/join")
    def api_join(room_id):
        body = request.get_json(silent=True) or {}
        name = (body.get("display_name") or "").strip()
        if not name:
            raise ServiceError("you need a display name")
        if len(name) > NAME_MAX:
            raise ServiceError(f"a display name is at most {NAME_MAX} characters")
        joined = app.service.join_room(room_id, name, body.get("class_id", ""),
                                       body.get("appearance"))
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
            "narrator": app.config.get("NARRATOR_NAME", "template"),
        })

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

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, threaded=True)
