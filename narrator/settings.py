# narrator/settings.py
"""The four inference knobs, in one place.

Every value has a default tuned for a 2-core CPU with no GPU, and every value
can be overridden from the environment without touching the code. A malformed
override is logged and ignored -- a bad environment variable must never stop
the game from starting.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

#: Generation length. On CPU this dominates everything else: time to a finished
#: paragraph is essentially linear in the tokens emitted.
DEFAULT_MAX_TOKENS = 60
#: KV cache size. The prompts are well under 400 tokens, so 1024 is ample and
#: costs far less memory bandwidth per step than 4096.
DEFAULT_N_CTX = 1024
DEFAULT_N_THREADS = 2
#: Prompt prefill batch. Larger is better as long as the prompt is short.
DEFAULT_N_BATCH = 512


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        log.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default
    if value <= 0:
        log.warning("%s=%r is not positive; using %d", name, raw, default)
        return default
    return value


def max_tokens() -> int:
    return _positive_int("CP_MAX_TOKENS", DEFAULT_MAX_TOKENS)


def n_ctx() -> int:
    return _positive_int("CP_N_CTX", DEFAULT_N_CTX)


def n_threads() -> int:
    return _positive_int("CP_N_THREADS", DEFAULT_N_THREADS)


def n_batch() -> int:
    return _positive_int("CP_N_BATCH", DEFAULT_N_BATCH)
