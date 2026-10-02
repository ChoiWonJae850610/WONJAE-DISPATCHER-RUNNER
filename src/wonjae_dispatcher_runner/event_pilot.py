from __future__ import annotations

import json
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .guards import require_sha
from .write_pilot import WritePilotError, changed_paths, git_head

ALLOWED_PATH = Path("docs/pilots/DISPATCHER_V2_WAKE_WRITE_001.md")
PILOT_ID = "CONTROL-DISPATCHER-V2-WAKE-WRITE-001"
MODEL_ACK = "CONTROL_WAKE_PILOT"
MODEL_OBSERVATION = "GitHub events can start bounded control-plane orchestration."


def parse_handoff(response: str) -> str:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise WritePilotError("event handoff response was not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"ack", "observation"}:
        raise WritePilotError("event handoff response shape was invalid")
    if payload["ack"] != MODEL_ACK or payload["observation"] != MODEL_OBSERVATION:
        raise WritePilotError("event handoff values were invalid")
    return payload["observation"]


def expected_document(expected_sha: str, observation: str = MODEL_OBSERVATION) -> str:
    sha = require_sha(expected_sha)
    if observation != MODEL_OBSERVATION:
        raise WritePilotError("unexpected event observation")
    return (
        "# Dispatcher v2 Event Wake Write Pilot 001\n\n"
        f"Pilot-ID: {PILOT_ID}\n"
        f"Source-Baseline-SHA: `{sha}`\n\n"
        "A trusted GitHub event started the public runner automatically.\n\n"
        "The runner restored the existing Codex session, validated a bounded structured "
        "handoff, and materialized this documentation-only change.\n\n"
        f"Codex observation: {observation}\n\n"
        "No product TASK, product repository, deployment, release, KDN route, runtime, "
        "build, EAS, device, or physical result is created or implied by this pilot.\n"
    )


def validate_diff(repo_path: Path, expected_sha: str) -> Path:
    paths = changed_paths(repo_path)
    allowed = ALLOWED_PATH.as_posix()
    if paths != [allowed]:
        raise WritePilotError(f"expected exactly one event path {allowed!r}, got {paths!r}")
    target = repo_path / ALLOWED_PATH
    if not target.is_file():
        raise WritePilotError("allowed event pilot document was not created")
    if target.read_text(encoding="utf-8") != expected_document(expected_sha):
        raise WritePilotError("event pilot document content drifted")
    return target


def _context(repo_path: Path) -> str:
    chunks: list[str] = []
    for relative in ("AGENTS.md", "PROJECT_RULES.md", "docs/pilots/DISPATCHER_V2_SIWC_WRITE_001.md"):
        path = repo_path / relative
        if not path.is_file():
            raise WritePilotError(f"required event context is missing: {relative}")
        chunks.append(f"--- {relative} ---\n{path.read_text(encoding='utf-8')[:12000]}")
    return "\n\n".join(chunks)


def run_event_pilot(repo_path: Path, expected_sha: str, codex_home: Path) -> Path:
    expected = require_sha(expected_sha)
    if git_head(repo_path) != expected or changed_paths(repo_path):
        raise WritePilotError("control checkout is not at the expected clean baseline")

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
        "Review the supplied control-plane context for this documentation-only event pilot. "
        "Do not edit files or call external systems. Return only the requested structured "
        f"acknowledgement.\n\n{_context(repo_path)}"
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
            config={"history": {"persistence": "none"}, "allow_login_shell": False},
        )
        result = thread.run(
            prompt,
            approval_mode=ApprovalMode.deny_all,
            output_schema=output_schema,
            sandbox=Sandbox.read_only,
        )

    status = str(getattr(result.status, "value", result.status)).lower()
    if status != "completed" or result.error is not None:
        raise WritePilotError("Codex event handoff did not complete successfully")
    observation = parse_handoff(result.final_response or "")

    if git_head(repo_path) != expected or changed_paths(repo_path):
        raise WritePilotError("repository changed during Codex event handoff")

    target = repo_path / ALLOWED_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected_document(expected, observation), encoding="utf-8")
    return validate_diff(repo_path, expected)
