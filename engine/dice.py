"""Seeded randomness. The single source of chance in the whole engine."""
from __future__ import annotations

import re
from typing import Sequence, TypeVar

# _random is the one place the engine touches the stdlib RNG; tests/test_purity.py
# allows it here by name because Dice is the injected seam everything else uses.
from random import Random as _Random

T = TypeVar("T")

_EXPR = re.compile(r"^(?:(\d+)d(\d+))?(?:([+-])([A-Za-z_]+|\d+))?$")


class Dice:
    """Deterministic dice. Construct with a seed; identical seeds replay identically."""

    def __init__(self, seed: int) -> None:
        self._rng = _Random(seed)
        self.seed = seed

    def d20(self) -> int:
        return self._rng.randint(1, 20)

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def choice(self, seq: Sequence[T]) -> T:
        return self._rng.choice(seq)

    def shuffle(self, seq: list) -> None:
        self._rng.shuffle(seq)

    def roll(self, expr: "str | int", mods: "dict[str, int] | None" = None) -> int:
        """Evaluate a dice expression: '2d6+RIGOR', '1d4-1', '10', or a bare int."""
        mods = mods or {}
        if isinstance(expr, int):
            return expr
        text = str(expr).replace(" ", "")
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        match = _EXPR.fullmatch(text)
        if not match or text == "":
            raise ValueError(f"bad dice expression: {expr!r}")
        count, faces, sign, term = match.groups()
        total = 0
        if count:
            total = sum(self._rng.randint(1, int(faces)) for _ in range(int(count)))
        if term is not None:
            value = int(term) if term.isdigit() else mods[term]
            total += value if sign == "+" else -value
        return total

    def state(self) -> tuple:
        return self._rng.getstate()

    def restore(self, state: tuple) -> None:
        self._rng.setstate(state)
