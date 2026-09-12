"""The narrator seam. Everything above this line is facts; below it is prose."""
from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable


@runtime_checkable
class Narrator(Protocol):
    name: str

    def narrate(self, job: dict) -> str:
        """Return prose describing an already-committed outcome."""


class FakeNarrator:
    """Deterministic narrator for tests. Never touches a model."""

    name = "fake"

    def __init__(self, text: str = "The bracket holds.") -> None:
        self._text = text
        self.jobs: list = []

    def narrate(self, job: dict) -> str:
        self.jobs.append(job)
        return self._text

    def stream(self, job: dict) -> Iterator[str]:
        self.jobs.append(job)
        for word in self._text.split(" "):
            yield word + " "
