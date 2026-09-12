import pytest
from app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({"ROOMS_ROOT": str(tmp_path), "SECRET_KEY": "test-secret",
                       "TESTING": True, "NARRATION": None})


@pytest.fixture
def client(app):
    return app.test_client()


def make_room(client, name="Kestrel", archetype="aircraft"):
    return client.post("/api/rooms", json={"name": name,
                                           "archetype": archetype}).get_json()["room_id"]
