from __future__ import annotations

import os
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
        "Read this repository in read-only mode and report its current Git HEAD commit SHA. "
        "Inspect the checkout using the available read-only tools. "
        "Return exactly the 40-character lowercase hexadecimal SHA and nothing else. "
        "Do not modify files, Git state, remotes, configuration, or external systems."
    )

    with Codex(config=config) as codex:
        account = codex.account(refresh_token=True)
        if account.requires_openai_auth:
            raise PilotError("Codex account session requires authentication")

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

    reported = (result.final_response or "").strip().lower()
    if reported != actual:
        raise PilotError(
            "Codex response did not exactly match the independently verified checkout SHA"
        )
    return reported
