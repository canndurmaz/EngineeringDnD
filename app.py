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

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=5000, threaded=True)
