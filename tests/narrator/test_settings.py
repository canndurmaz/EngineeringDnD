"""The four CPU-inference knobs: defaults, env overrides, and bad input."""
import importlib

import pytest

from narrator import settings

ENV_VARS = ("CP_MAX_TOKENS", "CP_N_CTX", "CP_N_THREADS", "CP_N_BATCH")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_tuned_for_two_cores():
    assert settings.max_tokens() == 60
    assert settings.n_ctx() == 1024
    assert settings.n_threads() == 2
    assert settings.n_batch() == 512


@pytest.mark.parametrize("name, reader, value", [
    ("CP_MAX_TOKENS", "max_tokens", 96),
    ("CP_N_CTX", "n_ctx", 2048),
    ("CP_N_THREADS", "n_threads", 4),
    ("CP_N_BATCH", "n_batch", 128),
])
def test_each_variable_overrides_its_default(monkeypatch, name, reader, value):
    monkeypatch.setenv(name, str(value))
    assert getattr(settings, reader)() == value


@pytest.mark.parametrize("bad", ["banana", "", "  ", "0", "-8", "3.5", "1e3"])
def test_invalid_values_fall_back_instead_of_raising(monkeypatch, bad):
    monkeypatch.setenv("CP_N_CTX", bad)
    assert settings.n_ctx() == settings.DEFAULT_N_CTX


def test_a_bad_variable_never_breaks_the_other_knobs(monkeypatch):
    monkeypatch.setenv("CP_N_CTX", "banana")
    monkeypatch.setenv("CP_MAX_TOKENS", "42")
    assert settings.n_ctx() == 1024 and settings.max_tokens() == 42


def test_sampling_uses_the_configured_ceiling(monkeypatch):
    monkeypatch.setenv("CP_MAX_TOKENS", "33")
    prompts = importlib.reload(importlib.import_module("narrator.prompts"))
    try:
        assert prompts.SAMPLING["max_tokens"] == 33
    finally:
        monkeypatch.delenv("CP_MAX_TOKENS")
        importlib.reload(prompts)
    assert prompts.SAMPLING["max_tokens"] == 60
