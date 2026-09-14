"""The session cookie's signing key is the only identity check in the app."""
import os
import stat

import pytest

from app import _secret_key


windows = pytest.mark.skipif(os.name == "nt",
                             reason="POSIX file modes do not apply on Windows")


def test_a_new_key_file_is_created(tmp_path):
    key = _secret_key(tmp_path)
    assert key and (tmp_path / "secret.key").read_text().strip() == key


def test_the_key_is_stable_across_calls(tmp_path):
    assert _secret_key(tmp_path) == _secret_key(tmp_path)


@windows
def test_a_new_key_file_is_not_readable_by_anyone_else(tmp_path):
    _secret_key(tmp_path)
    mode = stat.S_IMODE((tmp_path / "secret.key").stat().st_mode)
    assert mode == 0o600, f"expected 0600, found {mode:04o}"


@windows
def test_a_key_left_world_readable_by_an_older_build_is_tightened(tmp_path):
    path = tmp_path / "secret.key"
    path.write_text("legacy-key")
    os.chmod(path, 0o664)
    assert _secret_key(tmp_path) == "legacy-key"      # the key itself is kept
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@windows
def test_the_parent_directory_is_created_when_missing(tmp_path):
    nested = tmp_path / "instance"
    _secret_key(nested)
    assert stat.S_IMODE((nested / "secret.key").stat().st_mode) == 0o600
