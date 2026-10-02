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
