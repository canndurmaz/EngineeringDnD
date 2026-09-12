"""Strip anything the model invented that contradicts committed game state."""
from __future__ import annotations

import re

_SENTENCE = re.compile(r"[^.!?]+[.!?]")
_DICE_CLAIM = re.compile(
    r"\b(d20|dice|die|roll(s|ed|ing)?|natural\s+\d+|DC\s*\d+|modifier)\b",
    re.IGNORECASE)
_NUMBER = re.compile(r"\b\d+\b")
_PREAMBLE = re.compile(
    r"^\s*(sure|certainly|of course|here'?s?|here is|okay|ok)\b[^:]*:\s*",
    re.IGNORECASE)


def clean(text: str, allowed_numbers: set, max_sentences: int = 4) -> str:
    """Keep only sentences that invent neither a die roll nor a wrong number."""
    if not text:
        return ""
    text = _PREAMBLE.sub("", text.strip())
    text = text.strip().strip('"').strip("'").strip()

    sentences = [s.strip() for s in _SENTENCE.findall(text)] or \
        ([text.strip()] if text.strip() else [])
    allowed = {str(n) for n in allowed_numbers}

    kept = []
    for sentence in sentences:
        if _DICE_CLAIM.search(sentence):
            continue
        numbers = _NUMBER.findall(sentence)
        # Four-digit numbers read as years or part numbers, not game state.
        if any(n not in allowed and len(n) < 4 for n in numbers):
            continue
        kept.append(sentence)
        if len(kept) == max_sentences:
            break
    return " ".join(kept).strip()
