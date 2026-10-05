"""Wait for one exact validation run; observation failure never authorizes repair."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable

from .guards import require_sha

MAX_VALIDATION_WAIT_SECONDS = 3600  # The unchanged 60-minute product job is the outer bound.
READ_RECOVERY_SECONDS = 90
INTERVAL_SECONDS = 10
PENDING = {"queued", "in_progress", "waiting", "pending", "requested"}


class SourceValidationError(RuntimeError):
    def __init__(self, evidence: str, terminal_result: str = "FAILED"):
        super().__init__(evidence)
        self.terminal_result = terminal_result


def transient_read_error(error: Exception) -> bool:
    if isinstance(error, subprocess.TimeoutExpired):
        return True
    if not isinstance(error, subprocess.CalledProcessError):
        return False
    # gh's known HTTP/transport diagnostics only. Never retry auth/scope errors,
    # malformed JSON, identity mismatches, or arbitrary unknown command failures.
    stderr = error.stderr if isinstance(error.stderr, str) else ""
    if re.search(r"\bHTTP (?:429|500|502|503|504)\b", stderr):
        return True
    return any(marker in stderr for marker in (
        "TLS handshake timeout", "i/o timeout", "connection reset by peer",
    ))


def wait_exact_validation(
    read: Callable,
    *,
    repository: str,
    workflow_path: str,
    run_id: int,
    head_sha: str,
    event: str,
    seconds: float = MAX_VALIDATION_WAIT_SECONDS,
    clock=None,
    sleep=None,
) -> str:
    require_sha(head_sha)
    if type(run_id) is not int or run_id <= 0 or event not in {"pull_request", "push"}:
        raise ValueError("invalid exact validation request")
    if not 0 < seconds <= MAX_VALIDATION_WAIT_SECONDS:
        raise ValueError("validation wait must be positive and at most 3600 seconds")
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    deadline = clock() + seconds
    read_failure_since = None
    observed = "NOT_OBSERVED"
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise SourceValidationError(
                f"SOURCE_VALIDATION_TIMEOUT run={run_id} head={head_sha} observed={observed}"
            )
        if read_failure_since is not None:
            remaining = min(remaining, READ_RECOVERY_SECONDS - (clock() - read_failure_since))
            if remaining <= 0:
                raise SourceValidationError(
                    f"SOURCE_VALIDATION_READ_TIMEOUT run={run_id} head={head_sha}"
                )
        try:
            run = read(run_id, timeout_seconds=min(15, remaining))
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            if not transient_read_error(exc):
                raise SourceValidationError(
                    f"SOURCE_VALIDATION_READ_REJECTED run={run_id} head={head_sha}"
                ) from exc
            if read_failure_since is None:
                read_failure_since = clock()
            read_remaining = READ_RECOVERY_SECONDS - (clock() - read_failure_since)
            if read_remaining <= 0:
                raise SourceValidationError(
                    f"SOURCE_VALIDATION_READ_TIMEOUT run={run_id} head={head_sha}"
                ) from exc
            sleep(min(INTERVAL_SECONDS, read_remaining, max(0, deadline - clock())))
            continue
        read_failure_since = None
        if (
            not isinstance(run, dict)
            or run.get("id") != run_id
            or (run.get("repository") or {}).get("full_name") != repository
            or (run.get("head_repository") or {}).get("full_name") != repository
            or str(run.get("path", "")).split("@", 1)[0] != workflow_path
            or run.get("head_sha") != head_sha
            or run.get("event") != event
        ):
            raise SourceValidationError(
                f"SOURCE_VALIDATION_IDENTITY_MISMATCH run={run_id} expected_head={head_sha}"
            )
        observed = str(run.get("status") or "UNKNOWN")
        if observed == "completed":
            conclusion = run.get("conclusion")
            if conclusion in {"success", "failure"}:
                return str(conclusion)
            if conclusion not in {
                "cancelled", "action_required", "timed_out", "startup_failure",
                "neutral", "skipped", "stale",
            }:
                raise SourceValidationError(f"SOURCE_VALIDATION_INVALID_CONCLUSION run={run_id}")
            result = {"cancelled": "CANCELLED", "action_required": "MANUAL_REQUIRED"}.get(
                str(conclusion), "FAILED"
            )
            raise SourceValidationError(
                f"SOURCE_VALIDATION_NON_SOURCE_TERMINAL run={run_id} head={head_sha} "
                f"conclusion={conclusion}", result,
            )
        if observed not in PENDING or run.get("conclusion") is not None:
            raise SourceValidationError(f"SOURCE_VALIDATION_INVALID_STATE run={run_id}")
        sleep(min(INTERVAL_SECONDS, max(0, deadline - clock())))
