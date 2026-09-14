"""The per-room lock must make simultaneous actions safe."""
import threading
import pytest
from engine.classes import load_catalog
from engine.phases import load_archetypes, load_hazard_templates
from engine.rules import RuleError
from service import GameService, ServiceError


@pytest.fixture
def table(tmp_path):
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes())
    room_id = svc.create_room("Kestrel", "aircraft")
    a = svc.join_room(room_id, "Ada", "computer_scientist")
    b = svc.join_room(room_id, "Ben", "mechanical_technician")
    svc.start_game(room_id)
    return svc, room_id, a["player_id"], b["player_id"]


def test_simultaneous_actions_from_one_player_resolve_exactly_once(table):
    """Eight threads race as the SAME player; the lock must let exactly one through.

    All eight act as Ada deliberately. Mixing in Ben's threads would not test the
    lock: this is a turn-based game, so Ada's success legitimately hands the turn
    to Ben, whose thread then also succeeds - and how many alternations land before
    the threads drain is pure scheduling luck (observed 2 and 4 across runs).
    Racing one player is the deterministic form of the question.
    """
    svc, room_id, ada, _ = table
    results, errors = [], []
    barrier = threading.Barrier(8)

    def attempt():
        barrier.wait()
        try:
            results.append(svc.act(room_id, ada, "unit_test_barrage"))
        except (RuleError, ServiceError) as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)

    assert len(results) == 1, f"{len(results)} actions resolved; expected exactly 1"
    assert len(errors) == 7
    # The seven losers must be rejected by the turn gate, not by a crash.
    assert all("turn" in e.lower() for e in errors), errors


def test_focus_is_never_double_spent(table):
    svc, room_id, ada, _ = table
    before = svc.snapshot(room_id)["characters"][ada]["focus"]
    barrier = threading.Barrier(5)

    def attempt():
        barrier.wait()
        try:
            svc.act(room_id, ada, "unit_test_barrage")
        except (RuleError, ServiceError):
            pass

    threads = [threading.Thread(target=attempt) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)

    after = svc.snapshot(room_id)["characters"][ada]["focus"]
    assert after == before - 1


def test_event_sequence_numbers_never_collide(table):
    svc, room_id, ada, ben = table
    for _ in range(6):
        state = svc.snapshot(room_id)
        active = state["turn"]["order"][state["turn"]["turn_index"]]
        svc.end_turn(room_id, active)
    seqs = [e["seq"] for e in svc.events_since(room_id, 0)]
    assert len(seqs) == len(set(seqs))
    assert seqs == sorted(seqs)


def test_two_rooms_do_not_block_each_other(tmp_path):
    svc = GameService(str(tmp_path), load_catalog(), load_hazard_templates(),
                      load_archetypes())
    rooms = []
    for index in range(2):
        room_id = svc.create_room(f"R{index}", "car")
        player = svc.join_room(room_id, f"P{index}", "computer_scientist")
        svc.start_game(room_id)
        rooms.append((room_id, player["player_id"]))

    done = []

    def play(room_id, player_id):
        for _ in range(3):
            try:
                svc.end_turn(room_id, player_id)
            except ServiceError:
                # A solo party can burn out inside three rounds: the hazard attacks
                # after every full round, and with one player every turn IS a round.
                # Game-over is a legitimate outcome here, not a locking failure.
                break
        done.append(room_id)

    threads = [threading.Thread(target=play, args=pair) for pair in rooms]
    for t in threads: t.start()
    for t in threads: t.join(timeout=10)
    assert len(done) == 2
