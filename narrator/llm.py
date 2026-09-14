# narrator/llm.py
"""llama-cpp-python binding. Loaded lazily, inside the worker thread."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from narrator.prompts import GENESIS_SAMPLING, SAMPLING, build

log = logging.getLogger(__name__)


class ModelUnavailable(Exception):
    """The GGUF file is missing, or llama-cpp-python is not installed."""


def _default_loader(path: str, **kwargs):
    try:
        from llama_cpp import Llama
    except ImportError as exc:                  # optional dependency by design
        raise ModelUnavailable(
            "llama-cpp-python is not installed; run `make install-llm`") from exc
    return Llama(model_path=path, verbose=False, **kwargs)


class LlamaNarrator:
    name = "llm"

    def __init__(self, model_path: str, loader=None, n_ctx: int = 4096,
                 n_threads: int = 2, n_batch: int = 256) -> None:
        self.model_path = Path(model_path)
        self._loader = loader or _default_loader
        self._settings = {"n_ctx": n_ctx, "n_threads": n_threads,
                          "n_batch": n_batch}
        self._model = None
        self._guard = threading.Lock()

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def load(self):
        with self._guard:
            if self._model is None:
                if not self.available:
                    raise ModelUnavailable(f"no model at {self.model_path}")
                log.info("loading %s", self.model_path.name)
                self._model = self._loader(str(self.model_path), **self._settings)
            return self._model

    def narrate(self, job: dict) -> str:
        model = self.load()
        sampling = GENESIS_SAMPLING if job.get("kind") in ("genesis", "hazards") \
            else SAMPLING
        response = model.create_chat_completion(messages=build(job), **sampling)
        return response["choices"][0]["message"]["content"].strip()
