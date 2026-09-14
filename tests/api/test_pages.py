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


def test_the_session_cookie_is_samesite_lax(app):
    """/end-turn and /start take no body, so a cross-site form post would
    otherwise pass a player's turn for them."""
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_the_session_cookie_is_http_only(app):
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_the_issued_session_cookie_carries_both_flags(client):
    from tests.api.conftest import make_room
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/join",
                           json={"display_name": "Ada",
                                 "class_id": "computer_scientist"})
    header = response.headers.get("Set-Cookie", "")
    assert "SameSite=Lax" in header and "HttpOnly" in header
