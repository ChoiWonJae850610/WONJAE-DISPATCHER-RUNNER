import json
import stat

import pytest

from wonjae_dispatcher_runner.auth_store import (
    AuthStoreError,
    load_auth_json_for_rotation,
    restore_auth_json,
    restored_auth_shape,
    write_account_failure_output,
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


def test_auth_shape_reports_only_fields_not_token_values(capsys):
    payload = json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "synthetic-secret-a", "refresh_token": "synthetic-secret-r"},
    })
    assert restored_auth_shape(payload) == "CHATGPT_TOKEN_FIELDS_PRESENT"
    assert restored_auth_shape(
        '{"auth_mode":"chatgpt","tokens":{}}'
    ) == "CHATGPT_TOKEN_FIELDS_INCOMPLETE"
    assert restored_auth_shape(
        '{"auth_mode":"api-key","OPENAI_API_KEY":"synthetic"}'
    ) == "OTHER_OR_UNRECOGNIZED_MODE"
    assert restored_auth_shape("{}") == "OTHER_OR_UNRECOGNIZED_MODE"
    assert restored_auth_shape("not-json") == "INVALID_JSON"
    assert not capsys.readouterr().out


def test_auth_failure_output_uses_only_fixed_codes_and_regular_file(tmp_path):
    path = tmp_path / "actions-output"
    path.write_text("", encoding="utf-8")
    write_account_failure_output(str(path), "CODEX_AUTH_ACCOUNT_UNAVAILABLE")
    write_account_failure_output(str(path), "CODEX_AUTH_LOOKUP_ERROR")
    write_account_failure_output(str(path), "bad-secret-synthetic")
    assert path.read_text() == (
        "failure_code=CODEX_AUTH_ACCOUNT_UNAVAILABLE\\n"
        "failure_code=CODEX_AUTH_LOOKUP_ERROR\\n"
    ).replace("\\n", "\n")
    alternate = tmp_path / "shortcut"
    alternate.symlink_to(path)
    write_account_failure_output(str(alternate), "CODEX_AUTH_ACCOUNT_UNAVAILABLE")
    assert path.read_text().count("failure_code=") == 2
