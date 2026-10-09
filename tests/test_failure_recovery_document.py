"""Synthetic, offline tests for bounded deterministic failed-source document planning."""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from wonjae_dispatcher_runner.execution_state import load_product_execution_state
from wonjae_dispatcher_runner.failure_recovery_document import (
    RecoveryPreparationError,
    TimeoutEvidence,
    check_evidence,
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
    assert not parse_initial_turn_timeout(timeout_log().replace("INITIAL_TURN attempt=3", "junk"), SHA)
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
    assert "tokens" not in doc  # do not dump freeform source scopes
    assert "Image work" in doc and "Protected external R2 gate" in doc
    branch, doc_path, state_path = recovery_paths(identity)
    assert branch == f"docs/recovery-wafl-{RUN}"
    assert doc_path == f"docs/operations/RECOVERY-WAFL-CLOUD-RUNTIME-001-{RUN}.md"
    assert state_path == ".wonjae/execution-state.yaml"
    new = yaml.safe_load(new_yaml)
    assert new["next_action"]["type"] == "DECISION_REQUIRED"
    assert new["next_action"]["source_task_id"] is None
    assert new["after_source_success"] is None
    assert new["source_success_queue"] == []
    assert "개발" in new["next_action"]["owner_action"]
    (tmp_path / ".wonjae").mkdir()
    (tmp_path / state_path).write_text(new_yaml, encoding="utf-8")
    restored = load_product_execution_state(tmp_path, state_path, identity.project)
    assert restored.next_action.type == "DECISION_REQUIRED"
    assert restored.after_source_success is None


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
