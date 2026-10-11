"""Inert-by-default Codex diagnostic core; no credentials, network or CLI entrypoint.

The live observation function only accepts an injected SDK factory. There is NO
authenticated workflow or invocation of this function in the current runner.
It may be wired only by a separate, approved, trusted-main control-plane action.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ContextManager

from openai_codex import ApprovalMode, Sandbox

from .repair_timeout import RepairPlanTimeout, repair_plan_deadline

PROBE_PROMPT = (
    "Reply with the single word OK. Do not open files or use tools, shell, "
    "web search or external resources."
)
MAX_PROBE_SECONDS = 120
MAX_LOG_BYTES = 500_000
PHASES = frozenset({
    "sdk_start", "account_lookup", "thread_start", "turn_submit",
    "first_notification_wait", "first_notification", "turn_started",
    "agent_output_wait", "agent_output_seen", "turn_completed", "sdk_close",
})
_INITIAL_TURN = re.compile(
    r"\bINITIAL_TURN attempt=([1-3]) "
    r"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source=([0-9a-f]{40})\b"
)
_TIMEOUT_PHASE = re.compile(
    r"\bDIRECT_CODEX_TIMEOUT_PHASE phase=([a-z_]+) elapsed_s=([0-9]+)\b"
)
_EXHAUSTED = re.compile(r"\bINITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0\b")


@dataclass(frozen=True)
class ProbeResult:
    status: str
    last_phase: str
    elapsed_ms: int
    account_present: bool
    turn_started: bool
    first_agent_output: bool
    completed: bool


@dataclass(frozen=True)
class HistoricTimeoutResult:
    status: str
    attempts: int
    phase: str


def _safe_status(value: str) -> bool:
    return value in {
        "PASS", "PASS_NO_AGENT_OUTPUT", "AUTH_SESSION_MISSING",
        "TURN_NON_SUCCESS", "TURN_NO_TERMINAL",
        "TIMEOUT", "SDK_UNKNOWN_ERROR",
    }


def format_probe_result(result: ProbeResult) -> str:
    """Print only fixed categories and bounded timing; never SDK/model content."""
    if not _safe_status(result.status) or result.last_phase not in PHASES:
        raise ValueError("unsafe diagnostic category")
    if result.elapsed_ms < 0 or result.elapsed_ms > MAX_PROBE_SECONDS * 1000 + 10000:
        raise ValueError("invalid elapsed diagnostic time")
    return (
        f"SAFE_CODEX_DIAGNOSTIC status={result.status} "
        f"phase={result.last_phase} elapsed_ms={result.elapsed_ms} "
        f"account_present={str(result.account_present).lower()} "
        f"turn_started={str(result.turn_started).lower()} "
        f"agent_output_seen={str(result.first_agent_output).lower()} "
        f"turn_complete={str(result.completed).lower()}"
    )


def _is_agent_output(event: Any) -> bool:
    """Check only public method/type identity; do not read or serialize message text."""
    method = getattr(event, "method", None)
    if method == "item/agentMessage/delta":
        return isinstance(getattr(getattr(event, "payload", None), "delta", None), str) and bool(
            getattr(event.payload, "delta", "")
        )
    if method != "item/completed":
        return False
    payload = getattr(event, "payload", None)
    item = getattr(payload, "item", None)
    root = getattr(item, "root", item)
    return getattr(root, "type", None) in {"agentMessage", "agent_message"}


def _turn_completed_successfully(event: Any) -> bool:
    payload = getattr(event, "payload", None)
    turn = getattr(payload, "turn", None)
    status = getattr(turn, "status", None)
    status_value = getattr(status, "value", status)
    return status_value == "completed"


def probe_injected_sdk(
    *,
    sdk_factory: Callable[[], ContextManager[Any]],
    empty_workspace: Path,
    deadline_seconds: float = MAX_PROBE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    deadline: Callable[..., ContextManager[None]] = repair_plan_deadline,
) -> ProbeResult:
    """Single readonly streamed turn on a verified empty synthetic directory.

    This core cannot load credentials or create a client on its own. An eventual
    Owner-authorized adapter must supply an SDK configured with an ephemeral
    CODEX_HOME and scrubbed environment. Neither that adapter nor a workflow
    exists in this PR.
    """
    if deadline_seconds <= 0 or deadline_seconds > MAX_PROBE_SECONDS:
        raise ValueError("diagnostic wall-clock deadline exceeds safe bound")
    if empty_workspace.is_symlink() or not empty_workspace.is_dir():
        raise ValueError("diagnostic requires an independent synthetic workspace")
    if any(empty_workspace.iterdir()):
        raise ValueError("diagnostic synthetic workspace must be empty")

    started_at = clock()
    phase = "sdk_start"
    account_present = False
    turn_started = False
    first_agent_output = False
    completed = False
    status = "SDK_UNKNOWN_ERROR"

    try:
        with deadline(deadline_seconds):
            with sdk_factory() as codex:
                phase = "account_lookup"
                account = codex.account(refresh_token=False)
                if getattr(account, "account", None) is None:
                    status = "AUTH_SESSION_MISSING"
                else:
                    account_present = True
                    phase = "thread_start"
                    thread = codex.thread_start(
                        cwd=str(empty_workspace),
                        approval_mode=ApprovalMode.deny_all,
                        sandbox=Sandbox.read_only,
                        ephemeral=True,
                        config={"history": {"persistence": "none"}, "web_search": "disabled"},
                    )
                    phase = "turn_submit"
                    turn = thread.turn(
                        PROBE_PROMPT,
                        approval_mode=ApprovalMode.deny_all,
                        sandbox=Sandbox.read_only,
                    )
                    phase = "first_notification_wait"
                    status = "TURN_NO_TERMINAL"
                    for event in turn.stream():
                        method = getattr(event, "method", None)
                        if phase == "first_notification_wait":
                            phase = "first_notification"
                        if method == "turn/started":
                            turn_started = True
                            if not first_agent_output:
                                phase = "agent_output_wait"
                        if _is_agent_output(event):
                            first_agent_output = True
                            phase = "agent_output_seen"
                        if method == "turn/completed":
                            completed = _turn_completed_successfully(event)
                            status = ("PASS" if first_agent_output else "PASS_NO_AGENT_OUTPUT") if (
                                completed
                            ) else "TURN_NON_SUCCESS"
                            phase = "turn_completed"
                            break
                        if method == "turn/failed":
                            status = "TURN_NON_SUCCESS"
                            break
                if status in {"PASS", "PASS_NO_AGENT_OUTPUT"}:
                    phase = "sdk_close"
    except RepairPlanTimeout:
        status = "TIMEOUT"
    except Exception:
        # SDK exceptions may embed authentication/transport data: never log their text.
        status = "SDK_UNKNOWN_ERROR"

    elapsed = max(0, int((clock() - started_at) * 1000))
    return ProbeResult(
        status=status,
        last_phase=phase,
        elapsed_ms=min(elapsed, MAX_PROBE_SECONDS * 1000 + 10000),
        account_present=account_present,
        turn_started=turn_started,
        first_agent_output=first_agent_output,
        completed=completed,
    )


def classify_historic_log(raw: str) -> HistoricTimeoutResult:
    """Classify a trusted job log without emitting its source SHA or other lines."""
    if len(raw.encode("utf-8")) > MAX_LOG_BYTES:
        raise ValueError("diagnostic input exceeds bounded log size")
    attempts = {}
    for match in _INITIAL_TURN.finditer(raw):
        attempt = int(match.group(1))
        source_sha = match.group(2)
        if attempt in attempts and attempts[attempt] != source_sha:
            return HistoricTimeoutResult("INCONSISTENT_IDENTITY", len(attempts), "unknown")
        attempts[attempt] = source_sha
    if set(attempts) != {1, 2, 3} or len(set(attempts.values())) != 1:
        return HistoricTimeoutResult("INSUFFICIENT_EVIDENCE", len(attempts), "unknown")
    if not _EXHAUSTED.search(raw):
        return HistoricTimeoutResult("INSUFFICIENT_EVIDENCE", len(attempts), "unknown")

    observations = [m.group(1) for m in _TIMEOUT_PHASE.finditer(raw)]
    if observations and observations[-1] in PHASES:
        return HistoricTimeoutResult("TIMEOUT_PHASE_OBSERVED", 3, observations[-1])
    return HistoricTimeoutResult("LEGACY_TIMEOUT_PHASE_UNKNOWN", 3, "unknown")
