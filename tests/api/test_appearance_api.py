"""The appearance option lists, the SVG renderer, and appearance persistence."""
import appearance as appearance_lib
from tests.api.conftest import make_room


def test_options_endpoint_returns_all_five_lists(client):
    body = client.get("/api/appearance-options").get_json()
    assert set(body) == {"hair", "eyes", "outfit", "face", "skin"}
    for kind, entries in body.items():
        assert entries, f"{kind} list is empty"
        assert all(set(e) == {"id", "label"} for e in entries)


def test_options_labels_are_human_readable(client):
    hair = client.get("/api/appearance-options").get_json()["hair"]
    labels = {entry["id"]: entry["label"] for entry in hair}
    assert "SHORT_CURLY" not in labels.values()
    assert all("_" not in label for label in labels.values())
    if "SHORT_CURLY" in labels:
        assert labels["SHORT_CURLY"] == "Short curly"


def test_options_include_a_bald_hair_choice(client):
    hair = client.get("/api/appearance-options").get_json()["hair"]
    assert any(entry["id"] == "NONE" for entry in hair)


def test_options_offer_every_skin_tone(client):
    skin = client.get("/api/appearance-options").get_json()["skin"]
    assert len(skin) == 7


def test_avatar_endpoint_renders_an_svg(client):
    response = client.get("/api/avatar.svg?hair=BOB&eyes=HAPPY"
                          "&outfit=BLAZER_SHIRT&face=SMILE&skin=LIGHT")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("image/svg+xml")
    assert response.get_data(as_text=True).lstrip().startswith("<svg")


def test_avatar_is_cached_forever(client):
    response = client.get("/api/avatar.svg")
    assert response.headers["Cache-Control"] == "public, max-age=31536000"


def test_avatar_with_no_parameters_still_renders(client):
    response = client.get("/api/avatar.svg")
    assert response.status_code == 200
    assert b"<svg" in response.data


def test_a_bogus_parameter_falls_back_rather_than_500ing(client):
    response = client.get("/api/avatar.svg?hair=WIG&eyes=../../etc/passwd"
                          "&outfit=<script>&face=99&skin=__class__")
    assert response.status_code == 200
    assert response.get_data(as_text=True).lstrip().startswith("<svg")


def test_a_bogus_value_renders_the_same_as_the_default(client):
    bogus = client.get("/api/avatar.svg?hair=NOPE").get_data()
    default = client.get(f"/api/avatar.svg?hair={appearance_lib.DEFAULT['hair']}"
                         ).get_data()
    assert bogus == default


def test_join_stores_the_chosen_appearance(client):
    room_id = make_room(client)
    chosen = {"hair": "BOB", "eyes": "HAPPY", "outfit": "HOODIE",
              "face": "SERIOUS", "skin": "BROWN"}
    joined = client.post(f"/api/rooms/{room_id}/join", json={
        "display_name": "Ada", "class_id": "computer_scientist",
        "appearance": chosen}).get_json()
    assert joined["character"]["appearance"] == chosen


def test_appearance_survives_join_state_and_reload(client, app):
    room_id = make_room(client)
    chosen = {"hair": "DREADS", "eyes": "WINK", "outfit": "OVERALL",
              "face": "TWINKLE", "skin": "DARK_BROWN"}
    joined = client.post(f"/api/rooms/{room_id}/join", json={
        "display_name": "Ada", "class_id": "computer_scientist",
        "appearance": chosen}).get_json()
    player_id = joined["player_id"]

    state = client.get(f"/api/rooms/{room_id}/state").get_json()
    assert state["characters"][player_id]["appearance"] == chosen

    # ...and again from a database read that did not go through the cache.
    room = app.service._room(room_id)
    room.close()
    assert room.load_state()["characters"][player_id]["appearance"] == chosen


def test_joining_without_an_appearance_still_produces_one(client):
    room_id = make_room(client)
    joined = client.post(f"/api/rooms/{room_id}/join", json={
        "display_name": "Ada", "class_id": "computer_scientist"}).get_json()
    made = joined["character"]["appearance"]
    assert set(made) == {"hair", "eyes", "outfit", "face", "skin"}
    assert all(made.values())


def test_a_randomised_appearance_only_uses_offered_options(client):
    room_id = make_room(client)
    options = client.get("/api/appearance-options").get_json()
    joined = client.post(f"/api/rooms/{room_id}/join", json={
        "display_name": "Ada", "class_id": "computer_scientist"}).get_json()
    for kind, value in joined["character"]["appearance"].items():
        assert value in [entry["id"] for entry in options[kind]]


def test_an_unknown_appearance_value_on_join_falls_back(client):
    room_id = make_room(client)
    joined = client.post(f"/api/rooms/{room_id}/join", json={
        "display_name": "Ada", "class_id": "computer_scientist",
        "appearance": {"hair": "<img src=x>", "skin": "BROWN"}}).get_json()
    made = joined["character"]["appearance"]
    assert made["hair"] == appearance_lib.DEFAULT["hair"]
    assert made["skin"] == "BROWN"


def test_a_non_dict_appearance_on_join_is_ignored(client):
    room_id = make_room(client)
    response = client.post(f"/api/rooms/{room_id}/join", json={
        "display_name": "Ada", "class_id": "computer_scientist",
        "appearance": "hacker"})
    assert response.status_code == 200
    assert response.get_json()["character"]["appearance"]


def test_appearance_endpoints_reference_no_external_hosts(client):
    body = client.get("/api/avatar.svg").get_data(as_text=True)
    assert "http://" not in body.replace("http://www.w3.org", "")
    assert "https://" not in body
