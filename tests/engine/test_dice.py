import pytest
from engine.dice import Dice


def test_same_seed_produces_same_sequence():
    a, b = Dice(1234), Dice(1234)
    assert [a.d20() for _ in range(20)] == [b.d20() for _ in range(20)]


def test_different_seeds_diverge():
    a, b = Dice(1), Dice(2)
    assert [a.d20() for _ in range(20)] != [b.d20() for _ in range(20)]


def test_d20_stays_in_range():
    d = Dice(7)
    assert all(1 <= d.d20() <= 20 for _ in range(1000))


def test_roll_accepts_flat_integer_string():
    assert Dice(1).roll("10", {}) == 10


def test_roll_accepts_bare_int():
    assert Dice(1).roll(10, {}) == 10


def test_roll_accepts_negative_flat():
    assert Dice(1).roll("-3", {}) == -3


def test_roll_evaluates_dice_plus_stat():
    d = Dice(99)
    values = [d.roll("2d6+RIGOR", {"RIGOR": 3}) for _ in range(200)]
    assert min(values) >= 5 and max(values) <= 15


def test_roll_evaluates_dice_minus_flat():
    d = Dice(5)
    values = [d.roll("1d4-1", {}) for _ in range(200)]
    assert min(values) >= 0 and max(values) <= 3


def test_roll_with_unknown_stat_raises():
    with pytest.raises(KeyError):
        Dice(1).roll("1d6+MAGIC", {"RIGOR": 2})


def test_roll_with_garbage_expression_raises():
    with pytest.raises(ValueError):
        Dice(1).roll("two sixes", {})


def test_state_round_trip_resumes_the_sequence():
    d = Dice(42)
    [d.d20() for _ in range(5)]
    saved = d.state()
    expected = [d.d20() for _ in range(5)]
    d.restore(saved)
    assert [d.d20() for _ in range(5)] == expected
