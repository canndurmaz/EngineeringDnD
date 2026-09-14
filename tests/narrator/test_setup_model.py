from scripts.setup_model import (MIRROR_REPO, MODEL_FILENAME, OFFICIAL_REPO,
                                 resolve_source)


def test_filename_is_the_q4_k_m_quantisation():
    assert MODEL_FILENAME == "Llama-3.2-1B-Instruct-Q4_K_M.gguf"


def test_without_a_token_the_ungated_mirror_is_used():
    repo, filename = resolve_source(has_token=False)
    assert repo == MIRROR_REPO and filename == MODEL_FILENAME


def test_with_a_token_the_official_gated_repo_is_preferred():
    repo, _ = resolve_source(has_token=True)
    assert repo == OFFICIAL_REPO


def test_official_repo_is_the_meta_one():
    assert OFFICIAL_REPO == "meta-llama/Llama-3.2-1B-Instruct"
