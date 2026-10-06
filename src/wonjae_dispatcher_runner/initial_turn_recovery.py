"""Bound initial workspace-write turns without retaining a rejected worktree."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from .direct_worker import DirectWorkerError, changed_paths, git_head, git_metadata_snapshot

TIMEOUT_EXIT_CODE = 75
MAX_INITIAL_ATTEMPTS = 3


class InitialTurnTimeout(DirectWorkerError):
    """Only the exact per-turn deadline is eligible for replacement."""


def fresh_checkout(repo: Path, sha: str, branch: str) -> None:
    """Keep the abandoned checkout isolated; clone only committed Git objects."""
    abandoned = repo.with_name(f"{repo.name}-abandoned-{uuid.uuid4().hex}")
    repo.rename(abandoned)
    subprocess.run(
        ["git", "clone", "--no-hardlinks", "--no-checkout", str(abandoned), str(repo)],
        check=True, capture_output=True, timeout=60,
    )
    subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "checkout", branch],
        check=True, capture_output=True, timeout=60,
    )
    if git_head(repo) != sha or changed_paths(repo):
        raise DirectWorkerError("fresh checkout did not reproduce the original exact SHA")


def run_initial_turns(
    repo: Path,
    *,
    run_turn: Callable[[int], int],
    verify_authority: Callable[[], None],
    record: Callable[[int, str], None],
    replace_checkout: Callable[[Path, str, str], None] = fresh_checkout,
) -> int:
    """Return only a successful/non-timeout attempt; all other failures stop."""
    original_sha = git_head(repo)
    branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"], check=True,
        capture_output=True, text=True, timeout=20,
    ).stdout.strip()
    if not branch or changed_paths(repo):
        raise DirectWorkerError("initial recovery requires a clean named checkout")
    for attempt in range(1, MAX_INITIAL_ATTEMPTS + 1):
        if git_head(repo) != original_sha or changed_paths(repo):
            raise DirectWorkerError("initial recovery checkout identity changed")
        verify_authority()
        metadata = git_metadata_snapshot(repo)
        code = run_turn(attempt)
        # Security failures override timeout. Never silently discard protected mutations.
        if git_head(repo) != original_sha or git_metadata_snapshot(repo) != metadata:
            raise DirectWorkerError("initial recovery detected protected Git mutation")
        changed_paths(repo)
        verify_authority()
        if code != TIMEOUT_EXIT_CODE:
            record(attempt, "SUCCESS" if code == 0 else "FAILED_NOT_RETRYABLE")
            return code
        record(attempt, "INITIAL_CODEX_TIMEOUT_480_SECONDS")
        if attempt == MAX_INITIAL_ATTEMPTS:
            raise InitialTurnTimeout("INITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0")
        replace_checkout(repo, original_sha, branch)
    raise AssertionError("unreachable initial recovery state")
