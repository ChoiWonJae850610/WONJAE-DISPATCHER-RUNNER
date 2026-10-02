from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .guards import require_sha

_SHA_TOKEN_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{40})(?![0-9a-fA-F])")
_GIT_HEAD_COMMAND_RE = re.compile(r"\bgit\b.*\brev-parse\b.*\bHEAD\b", re.IGNORECASE)


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


def _normalized_status(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def extract_git_head_command_evidence(items: Iterable[Any], repo_path: Path) -> str:
    """Return the SHA proven by a successful Codex git rev-parse HEAD command item."""
    repo_resolved = repo_path.resolve()
    candidates: set[str] = set()

    for item in items:
        root = getattr(item, "root", item)
        if getattr(root, "type", None) != "commandExecution":
            continue

        command = str(getattr(root, "command", "") or "")
        if not _GIT_HEAD_COMMAND_RE.search(command):
            continue

        cwd_raw = str(getattr(root, "cwd", "") or "")
        try:
            cwd = Path(cwd_raw).resolve()
        except (OSError, RuntimeError):
            continue
        if cwd != repo_resolved:
            continue

        if getattr(root, "exit_code", None) != 0:
            continue
        if _normalized_status(getattr(root, "status", "")) != "completed":
            continue

        output = str(getattr(root, "aggregated_output", "") or "")
        matches = {match.group(1).lower() for match in _SHA_TOKEN_RE.finditer(output)}
        if len(matches) == 1:
            candidates.update(matches)

    if len(candidates) != 1:
        raise PilotError(
            "Codex turn did not contain exactly one successful git rev-parse HEAD command "
            "with one full SHA from the exact checkout directory"
        )
    return candidates.pop()


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
        "Inspect this exact checkout in read-only mode. "
        "You must run the shell command `git rev-parse HEAD` with this repository as the "
        "working directory. Then finish. "
        "Do not modify files, Git state, remotes, configuration, or external systems."
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

    reported = extract_git_head_command_evidence(result.items, repo_path)
    if reported != actual:
        raise PilotError(
            "Codex command evidence SHA did not match the independently verified checkout SHA"
        )
    return reported
