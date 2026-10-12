from __future__ import annotations

import json
import os
from pathlib import Path


class AuthStoreError(RuntimeError):
    """Raised when the protected Codex file credential cannot be restored."""


def prepare_codex_home(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    config = path / "config.toml"
    config.write_text(
        'cli_auth_credentials_store = "file"\n'
        "[history]\n"
        'persistence = "none"\n',
        encoding="utf-8",
    )
    config.chmod(0o600)
    return path


def restore_auth_json(codex_home: Path, payload: str) -> Path:
    if not payload.strip():
        raise AuthStoreError("CODEX_AUTH_JSON is empty")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AuthStoreError("CODEX_AUTH_JSON is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise AuthStoreError("CODEX_AUTH_JSON must contain a JSON object")

    prepare_codex_home(codex_home)
    target = codex_home / "auth.json"
    target.write_text(payload, encoding="utf-8")
    target.chmod(0o600)
    return target



# This is a non-secret, offline shape observation, NOT authentication proof.
# Never serialize values, fields or SDK account objects into Actions logs.
SAFE_ACCOUNT_FAILURE_CODES = frozenset({
    "CODEX_AUTH_ACCOUNT_UNAVAILABLE",
    "CODEX_AUTH_LOOKUP_ERROR",
})


def restored_auth_shape(payload: str) -> str:
    """Classify only expected field presence; never check expiry or online identity."""
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        return "INVALID_JSON"
    if not isinstance(value, dict):
        return "INVALID_ROOT"
    if value.get("auth_mode") != "chatgpt":
        return "OTHER_OR_UNRECOGNIZED_MODE"
    tokens = value.get("tokens")
    if not isinstance(tokens, dict):
        return "CHATGPT_TOKEN_FIELDS_INCOMPLETE"
    if all(isinstance(tokens.get(key), str) and bool(tokens[key])
           for key in ("access_token", "refresh_token")):
        return "CHATGPT_TOKEN_FIELDS_PRESENT"
    return "CHATGPT_TOKEN_FIELDS_INCOMPLETE"


def write_account_failure_output(step_output: str | None, failure_code: str) -> None:
    """Send a fixed code to the trusted GitHub Actions output, never secret data."""
    if not step_output or failure_code not in SAFE_ACCOUNT_FAILURE_CODES:
        return
    path = Path(step_output)
    if not path.is_file() or path.is_symlink():
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write("failure_code=" + failure_code + "\n")


def load_auth_json_for_rotation(codex_home: Path) -> str:
    target = codex_home / "auth.json"
    if not target.is_file():
        raise AuthStoreError("Codex did not produce an auth.json file")
    payload = target.read_text(encoding="utf-8")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AuthStoreError("Codex auth.json is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise AuthStoreError("Codex auth.json must contain a JSON object")
    return payload


def codex_home_from_env() -> Path:
    raw = os.environ.get("CODEX_HOME", "").strip()
    if not raw:
        raise AuthStoreError("CODEX_HOME is required")
    return Path(raw)
