from __future__ import annotations

import json
import subprocess
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .guards import require_sha


class PilotError(RuntimeError):
    """Raised when the read-only Codex pilot cannot prove the expected result."""


def git_head(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return require_sha(result.stdout.strip())


def parse_structured_sha(response: str) -> str:
    """Parse one full Git SHA from the SDK's structured final response."""
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise PilotError("Codex structured response was not valid JSON") from exc

    if not isinstance(payload, dict) or set(payload) != {"sha"}:
        raise PilotError("Codex structured response must contain only the sha field")

    raw_sha = payload["sha"]
    if not isinstance(raw_sha, str):
        raise PilotError("Codex structured sha field must be a string")

    try:
        return require_sha(raw_sha)
    except ValueError as exc:
        raise PilotError("Codex structured sha field was not a full 40-character SHA") from exc


def run_readonly_head_probe(repo_path: Path, expected_sha: str, codex_home: Path) -> str:
    expected = require_sha(expected_sha)
    actual = git_head(repo_path)
    if actual != expected:
        raise PilotError(f"checked-out SHA mismatch: expected {expected}, got {actual}")

    child_env = {
        "CODEX_HOME": str(codex_home),
        "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
    }
    config = CodexConfig(cwd=str(repo_path), env=child_env)

    prompt = (
        "Inspect this checkout in read-only mode. "
        "Run git rev-parse HEAD in this repository and return that exact full 40-character "
        "lowercase commit SHA in the requested structured output. "
        "Do not modify files, Git state, remotes, configuration, or external systems."
    )
    output_schema = {
        "type": "object",
        "properties": {
            "sha": {"type": "string"},
        },
        "required": ["sha"],
        "additionalProperties": False,
    }

    with Codex(config=config) as codex:
        account = codex.account(refresh_token=False)
        if account.account is None:
            raise PilotError("Codex account session is missing")

        thread = codex.thread_start(
            approval_mode=ApprovalMode.deny_all,
            cwd=str(repo_path),
            ephemeral=True,
            sandbox=Sandbox.read_only,
            config={
                "history": {"persistence": "none"},
                "allow_login_shell": False,
            },
        )
        result = thread.run(
            prompt,
            approval_mode=ApprovalMode.deny_all,
            output_schema=output_schema,
            sandbox=Sandbox.read_only,
        )

    reported = parse_structured_sha(result.final_response or "")
    if reported != actual:
        raise PilotError(
            "Codex structured response SHA did not match the independently verified checkout SHA"
        )
    return reported
