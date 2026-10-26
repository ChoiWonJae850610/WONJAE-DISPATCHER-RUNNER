"""Synthetic, offline tests for bounded deterministic failed-source document planning."""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from wonjae_dispatcher_runner.execution_state import load_product_execution_state
from wonjae_dispatcher_runner.failure_recovery_document import (
    MIXED_CAPACITY_CODE,
    RecoveryPreparationError,
    TimeoutEvidence,
    check_evidence,
    parse_initial_turn_mixed_capacity,
    parse_initial_turn_timeout,
    recovery_paths,
    render_recovery,
)

SHA = "a" * 40
RUN = 37938489783


def evidence():
    return TimeoutEvidence(
        project="WAFL",
        repository="ChoiWonJae850610/WAFL",
        branch="cloud-dev-v1",
        source_sha=SHA,
        runner_run_id=RUN,
        task_id="WAFL-CLOUD-RUNTIME-001",
    )


def approved_state():
    return {
        "schema_version": 1,
        "project": "WAFL",
        "next_action": {
            "type": "SOURCE_READY", "title": "Cloud environment readiness",
            "source_task_id": "WAFL-CLOUD-RUNTIME-001",
            "owner_action": "Use the authenticated Owner button",
            "source_scope": ["Implement bounded cloud checks; do not include tokens"],
        },
        "after_source_success": {
            "type": "SOURCE_READY", "title": "Image work",
            "source_task_id": "WAFL-CLOUD-IMAGES-002",
            "source_scope": ["camera and image source"],
        },
        "source_success_queue": [{
            "type": "DECISION_REQUIRED", "title": "Protected external R2 gate",
            "source_scope": [],
        }],
    }


def timeout_log(sha=SHA):
    return "\n".join([
        f"2026-10-09T13:49:33Z INITIAL_TURN attempt=1 "
        f"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={sha}",
        f"2026-10-09T13:58:33Z INITIAL_TURN attempt=2 "
        f"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={sha}",
        f"2026-10-09T14:07:28Z INITIAL_TURN attempt=3 "
        f"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={sha}",
        "INITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0",
    ])


def test_exact_attempt_sequence_must_be_complete_and_source_bound():
    assert parse_initial_turn_timeout(timeout_log(), SHA)
    assert not parse_initial_turn_timeout(timeout_log("b" * 40), SHA)
    assert not parse_initial_turn_timeout(timeout_log().replace("attempt=2", "attempt=1"), SHA)
    assert not parse_initial_turn_timeout(timeout_log().replace("commits=0", "commits=1"), SHA)
    absent = timeout_log().replace("INITIAL_TURN attempt=3", "junk")
    assert not parse_initial_turn_timeout(absent, SHA)
    assert not parse_initial_turn_timeout(timeout_log() * 2, SHA)
    assert not parse_initial_turn_timeout("untrusted logs", SHA)
    assert not parse_initial_turn_timeout("x" * 2_000_001, SHA)


def test_exact_recovery_document_and_non_executable_state(tmp_path):
    identity, state = evidence(), approved_state()
    before = yaml.safe_dump(state)
    doc, new_yaml = render_recovery(identity, state)
    assert yaml.safe_dump(state) == before  # deep copy, original unchanged
    assert "INITIAL_CODEX_TIMEOUT_EXHAUSTED" in doc
    assert "commits 0" in doc
    assert "source_scope" not in doc
    assert "Implement bounded cloud checks; do not include tokens" not in doc
    assert "Image work" in doc and "Protected external R2 gate" in doc
    branch, doc_path, state_path = recovery_paths(identity)
    assert branch == f"docs/recovery-wafl-{RUN}"
    assert doc_path == f"docs/operations/RECOVERY-WAFL-CLOUD-RUNTIME-001-{RUN}.md"
    assert state_path == ".wonjae/execution-state.yaml"
    new = yaml.safe_load(new_yaml)
    assert new["next_action"]["type"] == "SOURCE_READY"
    assert new["next_action"]["source_task_id"] == "WAFL-CLOUD-RUNTIME-001"
    assert new["next_action"]["source_scope"] == state["next_action"]["source_scope"]
    assert new["after_source_success"] == state["after_source_success"]
    assert new["source_success_queue"] == state["source_success_queue"]
    assert f"Runner #{RUN}" in new["next_action"]["owner_action"]
    assert "Codex" in new["next_action"]["owner_action"]
    assert "Codex transport" in doc
    (tmp_path / ".wonjae").mkdir()
    (tmp_path / state_path).write_text(new_yaml, encoding="utf-8")
    restored = load_product_execution_state(tmp_path, state_path, identity.project)
    assert restored.next_action.type == "SOURCE_READY"
    assert restored.after_source_success is not None


@pytest.mark.parametrize("change", [
    {"project": "KDN"}, {"project": "WAFL;rm"},
    {"repository": "ChoiWonJae850610/CLASSMO"},
    {"branch": "main"}, {"branch": "../evil"},
    {"source_sha": "z" * 40}, {"runner_run_id": -1},
    {"task_id": "../../oops"}, {"task_id": "CLASSMO-OTHER-001"},
    {"error_code": "UNKNOWN"}, {"attempts": 2},
])
def test_invalid_identity_fails_closed(change):
    with pytest.raises(RecoveryPreparationError):
        check_evidence(replace(evidence(), **change), approved_state())


@pytest.mark.parametrize("mutation", [
    lambda v: v.update({"schema_version": 999}),
    lambda v: v.update({"project": "CLASSMO"}),
    lambda v: v["next_action"].update({"type": "MANUAL_QA"}),
    lambda v: v["next_action"].update({"source_scope": []}),
    lambda v: v["next_action"].update({"source_task_id": "WAFL-OTHER-002"}),
    lambda v: v.update({"after_source_success": None}),
    lambda v: v.update({"source_success_queue": [{}] * 9}),
])
def test_unapproved_or_unbounded_state_fails_closed(mutation):
    value = approved_state()
    mutation(value)
    with pytest.raises(RecoveryPreparationError):
        render_recovery(evidence(), value)


def test_long_github_job_prefix_and_ansi_accept_only_three_exact_timeout_markers():
    long_prefix = "wafl / direct-worker\tRun isolated Codex Direct Worker\t" + (
        "2026-10-10T14:11:14.1718709Z " + "step-label-" * 17
    )
    text = timeout_log().replace(
        "2026-10-09T13:49:33Z ", long_prefix + "\x1b[32m"
    ).replace(
        "2026-10-09T13:58:33Z ", long_prefix + "\x1b[32m"
    ).replace(
        "2026-10-09T14:07:28Z ", long_prefix + "\x1b[32m"
    )
    assert parse_initial_turn_timeout(text, SHA)
    assert not parse_initial_turn_timeout(text.replace("attempt=2", "attempt=1"), SHA)
    assert not parse_initial_turn_timeout(text + "\n" + text, SHA)



def mixed_capacity_log(sha=SHA):
    """Synthetic equivalent of the verified #40321915512 safe diagnostic lines."""
    return "\n".join([
        "2026-10-26T12:30:32Z DIRECT_CODEX_TIMEOUT_PHASE phase=turn_run elapsed_s=480",
        "2026-10-26T12:30:33Z INITIAL_CODEX_TIMEOUT_480_SECONDS",
        f"2026-10-26T12:30:34Z INITIAL_TURN attempt=1 "
        f"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={sha}",
        "2026-10-26T12:39:05Z DIRECT_CODEX_TIMEOUT_PHASE phase=turn_run elapsed_s=480",
        "2026-10-26T12:39:06Z INITIAL_CODEX_TIMEOUT_480_SECONDS",
        f"2026-10-26T12:39:07Z INITIAL_TURN attempt=2 "
        f"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={sha}",
        "2026-10-26T12:45:09Z Traceback (most recent call last):",
        '2026-10-26T12:45:09Z File "/venv/openai_codex/_run.py", '
        "line 64, in _raise_for_failed_turn",
        "2026-10-26T12:45:09Z     raise RuntimeError(turn.error.message)",
        "2026-10-26T12:45:09Z "
        "RuntimeError: Selected model is at capacity. Please try a different model.",
        f"2026-10-26T12:45:11Z INITIAL_TURN attempt=3 "
        f"outcome=FAILED_NOT_RETRYABLE source={sha}",
        "2026-10-26T12:45:11Z ##[error]Process completed with exit code 1.",
    ]) + "\n"


def test_mixed_capacity_accepts_only_exact_two_timeouts_then_sdk_failure():
    log = mixed_capacity_log()
    assert parse_initial_turn_mixed_capacity(log, SHA)
    assert not parse_initial_turn_timeout(log, SHA)
    assert not parse_initial_turn_mixed_capacity(log, "b" * 40)
    assert not parse_initial_turn_mixed_capacity(timeout_log(), SHA)
    assert not parse_initial_turn_mixed_capacity("x" * 2_000_001, SHA)


@pytest.mark.parametrize(("old", "new"), [
    ("attempt=1 ", "attempt=3 "),
    ("attempt=2 ", "attempt=1 "),
    ("outcome=FAILED_NOT_RETRYABLE", "outcome=SUCCESS"),
    ("phase=turn_run elapsed_s=480", "phase=thread_start elapsed_s=480"),
    ("RuntimeError: Selected model is at capacity.", "RuntimeError: Authentication failed."),
    ("Please try a different model.", "Please try again."),
    ("Traceback (most recent call last):", "Some model output"),
    ("openai_codex/_run.py", "untrusted/run.py"),
    ("raise RuntimeError(turn.error.message)", "raise SomeOtherError()"),
    ("source=" + SHA, "source=" + "b" * 40),
])
def test_mixed_capacity_fails_closed_on_ambiguous_markers(old, new):
    # Replace just the first occurrence: mismatched source on any attempt
    # must not be accepted as three attempts on one original SHA.
    assert not parse_initial_turn_mixed_capacity(mixed_capacity_log().replace(old, new, 1), SHA)


def test_mixed_capacity_rejects_duplicates_other_failures_and_reordering():
    log = mixed_capacity_log()
    assert not parse_initial_turn_mixed_capacity(log + log.splitlines()[4] + "\n", SHA)
    assert not parse_initial_turn_mixed_capacity(log + log.splitlines()[9] + "\n", SHA)
    assert not parse_initial_turn_mixed_capacity(
        log.replace("INITIAL_TURN attempt=3", "INITIAL_TURN attempt=4"), SHA,
    )
    assert not parse_initial_turn_mixed_capacity(
        log.replace("FAILED_NOT_RETRYABLE", "INITIAL_CODEX_TIMEOUT_480_SECONDS"),
        SHA,
    )
    assert not parse_initial_turn_mixed_capacity(
        log.replace("12:45:09Z RuntimeError", "12:30:31Z RuntimeError").replace(
            log.splitlines()[9] + "\n", "", 1,
        ) + log.splitlines()[9] + "\n",
        SHA,
    )
    assert not parse_initial_turn_mixed_capacity(
        log + "\nINITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0", SHA,
    )


def test_mixed_docs_preserve_source_and_all_successor_gates():
    original = approved_state()
    identity = replace(evidence(), error_code=MIXED_CAPACITY_CODE)
    doc, state_yaml = render_recovery(identity, original)
    assert MIXED_CAPACITY_CODE in doc
    assert "two 480-second" in doc
    assert "model-capacity" in doc
    assert "third model-capacity" in doc
    assert "model-switch permission" in doc
    assert "not proven recovered" in doc
    assert "3회" not in yaml.safe_load(state_yaml)["next_action"]["owner_action"]
    restored = yaml.safe_load(state_yaml)
    assert restored["next_action"]["type"] == "SOURCE_READY"
    assert restored["next_action"]["source_task_id"] == original["next_action"]["source_task_id"]
    assert restored["next_action"]["source_scope"] == original["next_action"]["source_scope"]
    assert restored["after_source_success"] == original["after_source_success"]
    assert restored["source_success_queue"] == original["source_success_queue"]
    assert yaml.safe_load(state_yaml)["project"] == "WAFL"
    assert original["next_action"]["owner_action"] == "Use the authenticated Owner button"
