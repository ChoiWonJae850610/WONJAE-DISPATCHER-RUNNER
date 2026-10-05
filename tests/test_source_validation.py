import subprocess
from copy import deepcopy

import pytest

from wonjae_dispatcher_runner.source_validation import SourceValidationError, wait_exact_validation

SHA = "a" * 40


def run(status="completed", conclusion="success"):
    return {"id": 7, "repository": {"full_name": "owner/product"},
            "head_repository": {"full_name": "owner/product"},
            "path": ".github/workflows/test.yml", "head_sha": SHA,
            "event": "pull_request", "status": status, "conclusion": conclusion}


def wait(sequence, *, seconds=120):
    queue = iter(sequence)
    clock, observations = [0], []

    def read(number, **kwargs):
        observations.append((number, kwargs["timeout_seconds"]))
        value = next(queue)
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    return wait_exact_validation(
        read, repository="owner/product", workflow_path=".github/workflows/test.yml",
        run_id=7, head_sha=SHA, event="pull_request", seconds=seconds,
        clock=lambda: clock[0], sleep=lambda value: clock.__setitem__(0, clock[0] + value),
    ), clock[0], observations


def test_pending_readback_waits_same_run_without_repair_or_annotations_permission():
    outcome, elapsed, reads = wait([
        run("queued", None), run("in_progress", None), run(),
    ])
    assert outcome == "success" and elapsed == 20 and len(reads) == 3
    assert all(number == 7 and timeout <= 15 for number, timeout in reads)


def test_transient_get_errors_then_genuine_failure_is_the_only_repair_outcome():
    server_error = subprocess.CalledProcessError(1, ["gh"], stderr="gh: HTTP 503")
    timeout = subprocess.TimeoutExpired(["gh"], 15)
    outcome, elapsed, reads = wait([server_error, timeout, run(conclusion="failure")])
    assert outcome == "failure" and elapsed == 20 and len(reads) == 3


@pytest.mark.parametrize("conclusion,result", [
    ("cancelled", "CANCELLED"), ("action_required", "MANUAL_REQUIRED"),
    ("timed_out", "FAILED"), ("skipped", "FAILED"), ("neutral", "FAILED"),
])
def test_non_source_terminals_never_authorize_a_code_repair(conclusion, result):
    with pytest.raises(SourceValidationError) as caught:
        wait([run(conclusion=conclusion)])
    assert caught.value.terminal_result == result
    assert "NON_SOURCE_TERMINAL" in str(caught.value)


@pytest.mark.parametrize("field,value", [
    ("id", 8), ("head_sha", "b" * 40), ("event", "push"),
    ("path", ".github/workflows/wrong.yml"),
    ("repository", {"full_name": "owner/other"}),
    ("head_repository", {"full_name": "fork/product"}),
])
def test_identity_changes_fail_without_sleep(field, value):
    item = run("in_progress", None)
    item[field] = value
    with pytest.raises(SourceValidationError, match="IDENTITY_MISMATCH"):
        wait([item])  # Any attempted second read would exhaust the iterator.


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_validation_timeout_is_precise_and_never_a_repair_result(status):
    with pytest.raises(SourceValidationError, match="SOURCE_VALIDATION_TIMEOUT"):
        wait([run(status, None)] * 3, seconds=20)


@pytest.mark.parametrize("diagnostic", ["gh: HTTP 401", "gh: HTTP 403", "unknown failure"])
def test_authorization_or_unknown_read_error_is_not_retried(diagnostic):
    error = subprocess.CalledProcessError(1, ["gh"], stderr=diagnostic)
    with pytest.raises(SourceValidationError, match="READ_REJECTED"):
        wait([error])


def test_read_recovery_deadline_is_shared_and_bounded():
    error = subprocess.CalledProcessError(1, ["gh"], stderr="gh: HTTP 502")
    with pytest.raises(SourceValidationError, match="READ_TIMEOUT"):
        wait([error] * 10)


def test_initial_success_has_no_added_wait():
    outcome, elapsed, reads = wait([run()])
    assert outcome == "success" and elapsed == 0 and len(reads) == 1
