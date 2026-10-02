from __future__ import annotations

import subprocess
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .guards import require_sha


class PilotError(RuntimeError):
    """Raised when the read-only Codex pilot cannot prove the expected result."""


def _run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def git_head(repo_path: Path) -> str:
    return require_sha(_run_git(repo_path, "rev-parse", "HEAD").strip())


def git_status(repo_path: Path) -> str:
    return _run_git(repo_path, "status", "--porcelain=v1", "--untracked-files=all")


def _status_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def run_readonly_head_probe(repo_path: Path, expected_sha: str, codex_home: Path) -> str:
    """Run one authenticated read-only Codex turn and independently preserve Git identity."""
    expected = require_sha(expected_sha)

    before_head = git_head(repo_path)
    if before_head != expected:
        raise PilotError(f"checked-out SHA mismatch: expected {expected}, got {before_head}")

    before_status = git_status(repo_path)
    if before_status:
        raise PilotError("private control checkout was not clean before the Codex turn")

    child_env = {
        "CODEX_HOME": str(codex_home),
        "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
    }
    config = CodexConfig(cwd=str(repo_path), env=child_env)

    prompt = (
        "Read AGENTS.md and PROJECT_RULES.md from this checkout. "
        "In one short sentence, state which source should be treated as authoritative for "
        "current repository state. "
        "This is a read-only control-plane pilot. Do not modify files, Git state, remotes, "
        "configuration, credentials, or external systems."
    )

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
            sandbox=Sandbox.read_only,
        )

    if _status_value(result.status) != "completed":
        raise PilotError("Codex read-only turn did not complete successfully")
    if result.error is not None:
        raise PilotError("Codex read-only turn returned an error")
    if not (result.final_response or "").strip():
        raise PilotError("Codex read-only turn completed without a final response")

    after_head = git_head(repo_path)
    if after_head != before_head:
        raise PilotError("Git HEAD changed during the read-only Codex turn")

    after_status = git_status(repo_path)
    if after_status != before_status:
        raise PilotError("working tree changed during the read-only Codex turn")

    return after_head
