from __future__ import annotations

import subprocess
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .guards import require_sha

ALLOWED_PATH = Path("docs/pilots/DISPATCHER_V2_SIWC_WRITE_001.md")
PILOT_ID = "CONTROL-DISPATCHER-V2-WRITE-001"


class WritePilotError(RuntimeError):
    """Raised when the bounded write pilot violates its evidence contract."""


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def git_head(repo_path: Path) -> str:
    return require_sha(_git(repo_path, "rev-parse", "HEAD").strip())


def changed_paths(repo_path: Path) -> list[str]:
    output = _git(repo_path, "status", "--porcelain=v1", "--untracked-files=all")
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            raise WritePilotError("unexpected git status entry")
        path = line[3:]
        if " -> " in path:
            raise WritePilotError("renames are prohibited in the write pilot")
        paths.append(path)
    return paths


def expected_document(expected_sha: str) -> str:
    sha = require_sha(expected_sha)
    return (
        "# Dispatcher v2 SIWC Write Pilot 001\n\n"
        f"Pilot-ID: {PILOT_ID}\n"
        f"Source-Baseline-SHA: `{sha}`\n\n"
        "This documentation-only file was created by the authenticated "
        "WONJAE-DISPATCHER-RUNNER Codex write pilot.\n\n"
        "Evidence boundary: GitHub branch, commit, Draft PR, and exact-SHA Actions "
        "readback are authoritative. Model prose is not source-state evidence.\n\n"
        "No product TASK, product repository, deployment, release, KDN route, "
        "runtime, build, EAS, device, or physical result is created or implied by this pilot.\n"
    )


def validate_write_diff(repo_path: Path, expected_sha: str) -> Path:
    paths = changed_paths(repo_path)
    allowed = ALLOWED_PATH.as_posix()
    if paths != [allowed]:
        raise WritePilotError(f"expected exactly one changed path {allowed!r}, got {paths!r}")

    target = repo_path / ALLOWED_PATH
    if not target.is_file():
        raise WritePilotError("allowed pilot document was not created")

    if target.read_text(encoding="utf-8") != expected_document(expected_sha):
        raise WritePilotError("pilot document content drifted from the bounded expected content")

    return target


def run_codex_write_pilot(repo_path: Path, expected_sha: str, codex_home: Path) -> Path:
    expected = require_sha(expected_sha)
    if git_head(repo_path) != expected:
        raise WritePilotError("checkout SHA changed before the Codex write turn")
    if changed_paths(repo_path):
        raise WritePilotError("checkout must be clean before the Codex write turn")

    target_content = expected_document(expected)
    config = CodexConfig(
        cwd=str(repo_path),
        env={
            "CODEX_HOME": str(codex_home),
            "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
        },
    )
    prompt = (
        "This is an Owner-authorized documentation-only write pilot. "
        "First read AGENTS.md, PROJECT_RULES.md, and "
        "docs/pilots/DISPATCHER_V2_CODEX_CLOUD_001.md. "
        f"Then create exactly one new file: {ALLOWED_PATH.as_posix()}. "
        "Do not modify any other file. Do not create any product TASK. "
        "Do not commit, push, create a PR, deploy, release, change credentials, "
        "or use external systems. Write the new file with exactly the following "
        "UTF-8 Markdown content, including line breaks:\n\n"
        f"{target_content}"
    )

    with Codex(config=config) as codex:
        account = codex.account(refresh_token=False)
        if account.account is None:
            raise WritePilotError("Codex account session is missing")

        thread = codex.thread_start(
            approval_mode=ApprovalMode.deny_all,
            cwd=str(repo_path),
            ephemeral=True,
            sandbox=Sandbox.workspace_write,
            config={
                "history": {"persistence": "none"},
                "allow_login_shell": False,
            },
        )
        result = thread.run(
            prompt,
            approval_mode=ApprovalMode.deny_all,
            sandbox=Sandbox.workspace_write,
        )

    status = str(getattr(result.status, "value", result.status)).lower()
    if status != "completed" or result.error is not None:
        raise WritePilotError("Codex write turn did not complete successfully")
    if not (result.final_response or "").strip():
        raise WritePilotError("Codex write turn completed without a final response")

    if git_head(repo_path) != expected:
        raise WritePilotError("Codex changed Git HEAD, which is outside pilot authority")

    return validate_write_diff(repo_path, expected)
