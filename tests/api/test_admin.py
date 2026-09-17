"""The admin pane: off by default, password-gated, and careful about deletes."""
import logging
from pathlib import Path

import pytest

import app as app_module
from bots import BotRunner
from storage.index_db import IndexDB
from storage.room_db import RoomDB
from tests.api.conftest import make_room

PASSWORD = "correct horse"

ADMIN_ROUTES = [
    ("get", "/api/admin/rooms", None),
    ("post", "/api/admin/rooms", {"name": "X", "archetype": "aircraft"}),
    ("delete", "/api/admin/rooms/abcdef", {"confirm": "abcdef"}),
]


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_FAIL_DELAY", 0)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("CP_ADMIN_PASSWORD", PASSWORD)


@pytest.fixture
def admin(client, enabled):
    assert client.post("/api/admin/login",
                       json={"password": PASSWORD}).status_code == 200
    return client


def delete(client, room_id, confirm):
    return client.delete(f"/api/admin/rooms/{room_id}", json={"confirm": confirm})


# --- switched off ------------------------------------------------------------

def test_everything_is_404_when_the_password_is_unset(client, monkeypatch):
    monkeypatch.delenv("CP_ADMIN_PASSWORD", raising=False)
    assert client.get("/admin").status_code == 404
    assert client.post("/api/admin/login",
                       json={"password": ""}).status_code == 404
    assert client.post("/api/admin/logout").status_code == 404
    for method, url, body in ADMIN_ROUTES:
        assert getattr(client, method)(url, json=body).status_code == 404, url


def test_an_empty_password_counts_as_unset(client, monkeypatch):
    monkeypatch.setenv("CP_ADMIN_PASSWORD", "")
    assert client.get("/admin").status_code == 404
    assert client.post("/api/admin/login",
                       json={"password": ""}).status_code == 404


def test_a_forged_admin_flag_is_still_404_when_disabled(client, monkeypatch):
    monkeypatch.delenv("CP_ADMIN_PASSWORD", raising=False)
    with client.session_transaction() as sess:
        sess["admin"] = True
    for method, url, body in ADMIN_ROUTES:
        assert getattr(client, method)(url, json=body).status_code == 404, url


# --- login -------------------------------------------------------------------

def test_the_page_shows_a_login_form(client, enabled):
    body = client.get("/admin").get_data(as_text=True)
    assert 'id="login-form"' in body
    assert 'type="password"' in body
    assert 'data-admin="no"' in body


@pytest.mark.parametrize("password", ["wrong", "", None, 123, ["x"],
                                      PASSWORD + " ", PASSWORD.upper()])
def test_a_wrong_password_is_403(client, enabled, password):
    response = client.post("/api/admin/login", json={"password": password})
    assert response.status_code == 403
    with client.session_transaction() as sess:
        assert "admin" not in sess


def test_a_failed_login_is_slowed_down(client, enabled, monkeypatch):
    slept = []
    monkeypatch.setattr(app_module, "ADMIN_FAIL_DELAY", 0.5)
    monkeypatch.setattr(app_module.time, "sleep", slept.append)
    client.post("/api/admin/login", json={"password": "nope"})
    assert slept == [0.5]


def test_the_comparison_is_constant_time():
    source = Path(app_module.__file__).read_text()
    assert "hmac.compare_digest" in source


def test_the_right_password_makes_an_admin_session(client, enabled):
    response = client.post("/api/admin/login", json={"password": PASSWORD})
    assert response.status_code == 200
    with client.session_transaction() as sess:
        assert sess["admin"] is True
    assert 'data-admin="yes"' in client.get("/admin").get_data(as_text=True)
    assert client.get("/api/admin/rooms").status_code == 200


def test_logout_clears_the_flag(admin):
    assert admin.post("/api/admin/logout").status_code == 200
    assert admin.get("/api/admin/rooms").status_code == 403


# --- non-admins ----------------------------------------------------------------

@pytest.mark.parametrize("method,url,body", ADMIN_ROUTES)
def test_every_admin_route_rejects_a_non_admin(client, enabled, method, url, body):
    assert getattr(client, method)(url, json=body).status_code == 403


def test_a_seated_player_is_not_an_admin(client, enabled):
    room_id = make_room(client)
    client.post(f"/api/rooms/{room_id}/join",
                json={"display_name": "Ada", "class_id": "computer_scientist"})
    assert client.get("/api/admin/rooms").status_code == 403
    assert delete(client, room_id, room_id).status_code == 403
    assert room_id in [r["room_id"] for r in client.get("/api/rooms").get_json()["rooms"]]


def test_the_admin_flag_must_be_exactly_true(client, enabled):
    with client.session_transaction() as sess:
        sess["admin"] = "yes"
    assert client.get("/api/admin/rooms").status_code == 403


# --- listing and creating ------------------------------------------------------

def test_the_room_table_has_the_columns(admin):
    room_id = make_room(admin, name="Kestrel")
    admin.post(f"/api/rooms/{room_id}/join",
               json={"display_name": "Ada", "class_id": "computer_scientist"})
    admin.post(f"/api/rooms/{room_id}/bots", json={"class_id": "mechanical_technician"})
    rows = admin.get("/api/admin/rooms").get_json()["rooms"]
    row = next(r for r in rows if r["room_id"] == room_id)
    assert row["name"] == "Kestrel"
    assert row["archetype"] == "aircraft"
    assert row["status"] == "lobby"
    assert row["humans"] == 1 and row["bots"] == 1
    assert row["last_active"] > 0
    assert row["size_bytes"] > 0


def test_create_makes_a_room_that_shows_in_the_lobby(admin):
    response = admin.post("/api/admin/rooms",
                          json={"name": "Osprey", "archetype": "aircraft"})
    assert response.status_code == 201
    made = response.get_json()
    assert made["join_url"].endswith(f"/room/{made['room_id']}/join")
    lobby = admin.get("/api/rooms").get_json()["rooms"]
    assert any(r["room_id"] == made["room_id"] and r["name"] == "Osprey"
               for r in lobby)
    assert admin.get(f"/room/{made['room_id']}/join").status_code == 200


@pytest.mark.parametrize("body", [{"name": "", "archetype": "aircraft"},
                                  {"name": "x" * 41, "archetype": "aircraft"},
                                  {"name": "Ok", "archetype": "nope"},
                                  ["not", "a", "dict"]])
def test_create_validates_like_the_lobby(admin, body):
    assert admin.post("/api/admin/rooms", json=body).status_code == 400


# --- deleting ------------------------------------------------------------------

def test_delete_moves_the_folder_to_trash(admin, app):
    root = Path(app.config["ROOMS_ROOT"])
    room_id = make_room(admin)
    response = delete(admin, room_id, room_id)
    assert response.status_code == 200, response.get_json()
    assert not (root / room_id).exists()
    trashed = list((root / "_trash").iterdir())
    assert len(trashed) == 1
    assert trashed[0].name.startswith(f"{room_id}-")
    assert trashed[0].name.endswith("Z")
    assert (trashed[0] / "game.db").exists()
    # The moved database still holds the game.
    import sqlite3
    with sqlite3.connect(trashed[0] / "game.db") as conn:
        assert conn.execute("SELECT id FROM room").fetchone()[0] == room_id


def test_delete_removes_it_from_lobby_and_index(admin, app):
    room_id = make_room(admin)
    other = make_room(admin, name="Keep")
    assert delete(admin, room_id, room_id).status_code == 200
    lobby = [r["room_id"] for r in admin.get("/api/rooms").get_json()["rooms"]]
    assert room_id not in lobby and other in lobby
    admin_rows = [r["room_id"] for r in admin.get("/api/admin/rooms").get_json()["rooms"]]
    assert room_id not in admin_rows
    assert room_id not in [r["room_id"] for r in app.service.index.list_rooms()]
    assert room_id not in app.service._rooms
    assert room_id not in app.service._locks


def test_requests_for_a_deleted_room_are_400_not_500(admin):
    room_id = make_room(admin)
    admin.post(f"/api/rooms/{room_id}/join",
               json={"display_name": "Ada", "class_id": "computer_scientist"})
    admin.get(f"/api/rooms/{room_id}/state")        # warm the RoomDB cache
    assert delete(admin, room_id, room_id).status_code == 200
    calls = [
        ("get", f"/api/rooms/{room_id}/state", None),
        ("get", f"/api/rooms/{room_id}/chat", None),
        ("get", f"/api/rooms/{room_id}/stream?once=1", None),
        ("post", f"/api/rooms/{room_id}/join",
         {"display_name": "Bo", "class_id": "mechanical_technician"}),
        ("post", f"/api/rooms/{room_id}/start", None),
        ("post", f"/api/rooms/{room_id}/action", {"ability_id": "x"}),
        ("post", f"/api/rooms/{room_id}/end-turn", None),
        ("post", f"/api/rooms/{room_id}/chat", {"body": "hi"}),
        ("post", f"/api/rooms/{room_id}/skip-dm", None),
        ("post", f"/api/rooms/{room_id}/bots", {"class_id": "mechanical_technician"}),
        ("post", f"/api/rooms/{room_id}/level-choice", {"stat": "RIGOR"}),
    ]
    for method, url, body in calls:
        response = getattr(admin, method)(url, json=body)
        assert response.status_code == 400, (url, response.status_code)
        if "/action" not in url:        # that one checks the ability name first
            assert "no such room" in response.get_json()["error"]
    assert admin.get(f"/room/{room_id}").status_code == 404
    assert admin.get(f"/room/{room_id}/join").status_code == 404
    # A second delete is a clean 400 too.
    assert delete(admin, room_id, room_id).status_code == 400


def test_delete_requires_the_typed_code(admin, app):
    room_id = make_room(admin)
    for confirm in [None, "", room_id.upper(), room_id[:-1], "yes", 1]:
        response = admin.delete(f"/api/admin/rooms/{room_id}",
                                json={} if confirm is None else {"confirm": confirm})
        assert response.status_code == 400, confirm
    assert admin.delete(f"/api/admin/rooms/{room_id}").status_code == 400
    assert (Path(app.config["ROOMS_ROOT"]) / room_id / "game.db").exists()


def test_delete_of_an_unknown_or_odd_name_is_400(admin, app):
    root = Path(app.config["ROOMS_ROOT"])
    make_room(admin)
    delete(admin, make_room(admin), None)   # no-op, mismatch
    for bad in ["nosuch", "_trash", "index.db"]:
        assert delete(admin, bad, bad).status_code == 400, bad
    assert (root / "index.db").exists()


def test_delete_drops_queue_jobs_gate_and_notifies_the_table(app, admin):
    class Queue:
        dropped = []

        def submit(self, job):
            pass

        def drop_room(self, room_id):
            self.dropped.append(room_id)

    app.service.queue = Queue()
    room_id = make_room(admin)
    app.service._dm_gate[room_id] = {"event_seq": 1, "deadline": 1e18}
    listener = app.broker.subscribe(room_id)
    assert delete(admin, room_id, room_id).status_code == 200
    assert Queue.dropped == [room_id]
    assert room_id not in app.service._dm_gate
    event = listener.get_nowait()
    assert event["kind"] == "room_closed"
    assert event["message"] == "This room was closed by an admin."


def test_room_closed_frame_has_no_id():
    frame = app_module.sse_frame(7, "room_closed", {"message": "m"})
    assert "id:" not in frame and "event: room_closed" in frame


def test_the_bot_runner_skips_a_deleted_room_quietly(app, admin, caplog):
    room_id = make_room(admin)
    admin.post(f"/api/rooms/{room_id}/join",
               json={"display_name": "Ada", "class_id": "computer_scientist"})
    admin.post(f"/api/rooms/{room_id}/start")
    runner = BotRunner(app.service, app.catalog, delay=0)
    stale = app.service.list_rooms()
    assert delete(admin, room_id, room_id).status_code == 200
    with caplog.at_level(logging.INFO):
        assert runner.run_once() == 0
        # A listing taken before the delete still names the room.
        runner._active_rooms = lambda: stale
        assert runner.run_once() == 0
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]


# --- trash is invisible ----------------------------------------------------------

def _trash_room(root: Path):
    RoomDB.create(str(root), "_trash", "Ghost", "aircraft", 1).close()
    RoomDB.create(str(root / "_trash"), "gone", "Gone", "aircraft", 1).close()


def test_list_room_ids_ignores_trash(tmp_path):
    RoomDB.create(str(tmp_path), "live01", "Live", "aircraft", 1).close()
    _trash_room(tmp_path)
    assert RoomDB.list_room_ids(str(tmp_path)) == ["live01"]


def test_rebuild_ignores_trash(tmp_path):
    room = RoomDB.create(str(tmp_path), "live01", "Live", "aircraft", 1)
    room.close()
    _trash_room(tmp_path)
    index = IndexDB(str(tmp_path))
    assert index.rebuild() == 1
    assert [r["room_id"] for r in index.list_rooms()] == ["live01"]


# --- the page ------------------------------------------------------------------

def test_the_admin_table_escapes_a_malicious_room_name(admin):
    evil = '<img src=x onerror=alert(1)>"\''
    room_id = make_room(admin, name=evil[:40])
    rows = admin.get("/api/admin/rooms").get_json()["rooms"]
    assert any(r["room_id"] == room_id for r in rows)
    # The page itself never renders room names server-side...
    page = admin.get("/admin").get_data(as_text=True)
    assert "<img src=x" not in page
    # ...and the script sends every interpolation through esc().
    js = admin.get("/static/js/admin.js").get_data(as_text=True)
    assert "const esc = (text)" in js
    assert "${esc(r.name)}" in js
    assert "${r.name}" not in js
    assert "encodeURIComponent" in js
    import re
    for expr in re.findall(r"\$\{([^}]*)\}", js):
        expr = expr.strip()
        assert (expr.startswith(("esc(", "attr(", "encodeURIComponent("))
                or expr in {"n", "code", "(n / 1024).toFixed(1)",
                            "(n / 1024 / 1024).toFixed(1)",
                            "response.status"}), expr


def test_the_page_matches_the_console_theme(client, enabled):
    body = client.get("/admin").get_data(as_text=True)
    assert "css/theme.css" in body and "js/admin.js" in body
    css = client.get("/static/css/theme.css").get_data(as_text=True)
    block = css[css.index("/* --- admin pane"):]
    assert "#" not in block   # no new colours


def test_the_table_handles_room_closed(client):
    js = client.get("/static/js/game.js").get_data(as_text=True)
    assert 'addEventListener("room_closed"' in js
    assert "This room was closed by an admin." in js
    handler = js[js.index("function closeRoom"):]
    handler = handler[:handler.index("\n}\n")]
    assert "disabled = true" in handler
    assert "textContent" in handler and "innerHTML" not in handler


def test_a_stale_cached_room_is_a_400_after_delete(admin, app):
    room_id = make_room(admin)
    stale = app.service._room(room_id)
    assert delete(admin, room_id, room_id).status_code == 200
    app.service._rooms[room_id] = stale          # a read that raced the delete
    response = admin.get(f"/api/rooms/{room_id}/state")
    assert response.status_code == 400
    assert room_id not in app.service._rooms
