import pytest
from narrator.llm import LlamaNarrator, ModelUnavailable


class StubModel:
    def __init__(self, text="The bracket holds.", raise_on_call=False):
        self.text = text
        self.raise_on_call = raise_on_call
        self.calls = []

    def create_chat_completion(self, messages, **kwargs):
        if self.raise_on_call:
            raise RuntimeError("inference failed")
        self.calls.append((messages, kwargs))
        return {"choices": [{"message": {"content": self.text}}]}


def loader_for(model):
    def _load(path, **kwargs):
        _load.kwargs = kwargs
        return model
    return _load


def turn_job():
    return {"kind": "turn", "premise": "p", "phase": "Design",
            "actor_name": "Ada", "actor_class": "Computer Scientist",
            "ability_name": "Refactor", "outcome": "success", "natural": 12,
            "total": 15, "dc": 13, "hazard": None, "changes": []}


def test_name_identifies_the_source(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    assert LlamaNarrator(str(path), loader=loader_for(StubModel())).name == "llm"


def test_available_is_false_when_the_file_is_missing(tmp_path):
    assert LlamaNarrator(str(tmp_path / "absent.gguf")).available is False


def test_available_is_true_when_the_file_exists(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    assert LlamaNarrator(str(path), loader=loader_for(StubModel())).available is True


def test_narrate_without_a_model_file_raises(tmp_path):
    with pytest.raises(ModelUnavailable):
        LlamaNarrator(str(tmp_path / "absent.gguf")).narrate(turn_job())


def test_narrate_returns_the_model_text(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    narrator = LlamaNarrator(str(path),
                             loader=loader_for(StubModel("It holds.")))
    assert narrator.narrate(turn_job()) == "It holds."


def test_model_is_loaded_once_and_reused(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    model = StubModel()
    narrator = LlamaNarrator(str(path), loader=loader_for(model))
    narrator.narrate(turn_job())
    narrator.narrate(turn_job())
    assert len(model.calls) == 2          # two generations, one load


def test_loader_receives_the_cpu_tuned_settings(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    load = loader_for(StubModel())
    LlamaNarrator(str(path), loader=load, n_ctx=4096, n_threads=2).narrate(turn_job())
    assert load.kwargs["n_ctx"] == 4096 and load.kwargs["n_threads"] == 2


def test_inference_failure_propagates_for_the_worker_to_catch(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    narrator = LlamaNarrator(str(path),
                             loader=loader_for(StubModel(raise_on_call=True)))
    with pytest.raises(RuntimeError):
        narrator.narrate(turn_job())


def test_genesis_job_gets_a_larger_token_budget(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    model = StubModel()
    LlamaNarrator(str(path), loader=loader_for(model)).narrate(
        {"kind": "genesis", "room_name": "Kestrel", "archetype_hint": "a car"})
    assert model.calls[0][1]["max_tokens"] > 140


def test_loader_gets_the_cpu_tuned_defaults(tmp_path):
    """A small KV cache costs less memory bandwidth per step; a bigger batch
    prefills the (short) prompt faster."""
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    load = loader_for(StubModel())
    LlamaNarrator(str(path), loader=load).narrate(turn_job())
    assert load.kwargs == {"n_ctx": 1024, "n_threads": 2, "n_batch": 512}


def test_explicit_arguments_still_beat_the_defaults(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    load = loader_for(StubModel())
    LlamaNarrator(str(path), loader=load, n_ctx=8192, n_batch=64).narrate(turn_job())
    assert load.kwargs["n_ctx"] == 8192 and load.kwargs["n_batch"] == 64


def test_the_environment_can_retune_the_loader(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_N_CTX", "2048")
    monkeypatch.setenv("CP_N_THREADS", "4")
    monkeypatch.setenv("CP_N_BATCH", "256")
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    load = loader_for(StubModel())
    LlamaNarrator(str(path), loader=load).narrate(turn_job())
    assert load.kwargs == {"n_ctx": 2048, "n_threads": 4, "n_batch": 256}


def test_an_unparseable_environment_value_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_N_CTX", "banana")
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    load = loader_for(StubModel())
    LlamaNarrator(str(path), loader=load).narrate(turn_job())     # must not raise
    assert load.kwargs["n_ctx"] == 1024
