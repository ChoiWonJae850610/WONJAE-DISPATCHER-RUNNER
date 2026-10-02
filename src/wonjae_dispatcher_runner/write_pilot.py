from __future__ import annotations

import json
import subprocess
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .guards import require_sha

ALLOWED_PATH = Path("docs/pilots/DISPATCHER_V2_SIWC_WRITE_001.md")
PILOT_ID = "CONTROL-DISPATCHER-V2-WRITE-001"
MODEL_ACK = "AUTHORIZED_CONTROL_WRITE_PILOT"
MODEL_OBSERVATION = "GitHub exact-SHA evidence remains authoritative for current source state."


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


def parse_model_handoff(response: str) -> str:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise WritePilotError("Codex handoff response was not valid JSON") from exc

    if not isinstance(payload, dict) or set(payload) != {"ack", "observation"}:
        raise WritePilotError("Codex handoff response shape was invalid")
    if payload["ack"] != MODEL_ACK:
        raise WritePilotError("Codex did not return the required pilot acknowledgement")
    if payload["observation"] != MODEL_OBSERVATION:
        raise WritePilotError("Codex returned an unexpected pilot observation")
    return payload["observation"]


def expected_document(expected_sha: str, observation: str = MODEL_OBSERVATION) -> str:
    sha = require_sha(expected_sha)
    if observation != MODEL_OBSERVATION:
        raise WritePilotError("unexpected observation value")
    return (
        "# Dispatcher v2 SIWC Write Pilot 001\n\n"
        f"Pilot-ID: {PILOT_ID}\n"
        f"Source-Baseline-SHA: `{sha}`\n\n"
        "This documentation-only file was materialized by the trusted runner after an "
        "authenticated Codex handoff.\n\n"
        f"Codex observation: {observation}\n\n"
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


def _bounded_context(repo_path: Path) -> str:
    sections: list[str] = []
    for relative in ("AGENTS.md", "PROJECT_RULES.md"):
        path = repo_path / relative
        if not path.is_file():
            raise WritePilotError(f"required control context is missing: {relative}")
        text = path.read_text(encoding="utf-8")
        sections.append(f"--- {relative} ---\n{text[:12000]}")
    return "\n\n".join(sections)


def run_codex_write_pilot(repo_path: Path, expected_sha: str, codex_home: Path) -> Path:
    expected = require_sha(expected_sha)
    if git_head(repo_path) != expected:
        raise WritePilotError("checkout SHA changed before the Codex handoff turn")
    if changed_paths(repo_path):
        raise WritePilotError("checkout must be clean before the Codex handoff turn")

    context = _bounded_context(repo_path)
    config = CodexConfig(
        cwd=str(repo_path),
        env={
            "CODEX_HOME": str(codex_home),
            "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
        },
    )
    output_schema = {
        "type": "object",
        "properties": {
            "ack": {"type": "string", "enum": [MODEL_ACK]},
            "observation": {"type": "string", "enum": [MODEL_OBSERVATION]},
        },
        "required": ["ack", "observation"],
        "additionalProperties": False,
    }
    prompt = (
        "This is an Owner-authorized DEV-CONTROL documentation-only orchestration pilot. "
        "Review the supplied control-plane context. Do not edit files or call external systems. "
        "Return only the requested structured acknowledgement.\n\n"
        f"{context}"
    )

    with Codex(config=config) as codex:
        account = codex.account(refresh_token=False)
        if account.account is None:
            raise WritePilotError("Codex account session is missing")

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

    status = str(getattr(result.status, "value", result.status)).lower()
    if status != "completed" or result.error is not None:
        raise WritePilotError("Codex handoff turn did not complete successfully")
    observation = parse_model_handoff(result.final_response or "")

    if git_head(repo_path) != expected or changed_paths(repo_path):
        raise WritePilotError("repository changed during the read-only Codex handoff")

    target = repo_path / ALLOWED_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected_document(expected, observation), encoding="utf-8")
    return validate_write_diff(repo_path, expected)
