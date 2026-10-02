import json
import stat

import pytest

from wonjae_dispatcher_runner.auth_store import (
    AuthStoreError,
    load_auth_json_for_rotation,
    restore_auth_json,
)


def test_restore_auth_json_writes_private_file(tmp_path) -> None:
    payload = json.dumps({"auth_mode": "chatgpt", "tokens": {"example": "redacted"}})
    target = restore_auth_json(tmp_path / "codex", payload)

    assert json.loads(target.read_text())["auth_mode"] == "chatgpt"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_restore_auth_json_rejects_invalid_json(tmp_path) -> None:
    with pytest.raises(AuthStoreError):
        restore_auth_json(tmp_path / "codex", "not-json")


def test_rotation_load_requires_auth_file(tmp_path) -> None:
    with pytest.raises(AuthStoreError):
        load_auth_json_for_rotation(tmp_path)
