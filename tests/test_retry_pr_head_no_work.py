"""No-work retry must not conceal an actual failed canonical PR-head CI."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.direct_worker import DirectWorkerRoute, _prompt
from wonjae_dispatcher_runner.execution_state import ExecutionAction, ProductExecutionState
from wonjae_dispatcher_runner.retry_validation_evidence import (
    RetryEvidenceError,
    classify_retry_pr_head,
    exact_failed_job_id,
    main,
)

SHA = "a" * 40
REPO = "owner/product"
WF = "product-validation.yml"


def run(id=101, *, status="completed", conclusion="failure", sha=SHA, **override):
    return {
        "id": id,
        "head_sha": sha,
        "event": "pull_request",
        "status": status,
        "conclusion": conclusion,
        "path": ".github/workflows/" + WF,
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
        **override,
    }


def classify(rows, *, total=None):
    return classify_retry_pr_head(
        {"total_count": len(rows) if total is None else total, "workflow_runs": rows},
        source_sha=SHA,
        repository=REPO,
        workflow_file=WF,
    )


def test_exact_failed_pr_head_requires_repair_but_no_ci_keeps_manual_retry_route():
    assert classify([run()]) == ("FAILED", 101)
    assert classify([]) == ("NONE", 0)


def test_newer_exact_success_supersedes_earlier_failure_and_unsorted_api_history():
    assert classify([run(99), run(105, conclusion="success")]) == ("PASS", 105)
    assert classify([run(107, conclusion="failure"), run(105, conclusion="success")]) == (
        "FAILED", 107
    )


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "skipped", "action_required"])
def test_non_success_non_repairable_terminal_is_not_success(conclusion):
    assert classify([run(conclusion=conclusion)]) == ("BLOCKED", 101)


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested"])
def test_nonterminal_validation_is_not_source_success(status):
    assert classify([run(status=status, conclusion=None)]) == ("BLOCKED", 101)


@pytest.mark.parametrize("override", [
    {"sha": "b" * 40},
    {"event": "push"},
    {"path": ".github/workflows/other.yml"},
    {"repository": {"full_name": "fork/other"}},
    {"head_repository": {"full_name": "fork/other"}},
    {"id": 0},
    {"id": True},
])
def test_cross_head_workflow_repo_or_invalid_id_cannot_authorize_retry(override):
    special = {k: v for k, v in override.items() if k != "sha"}
    with pytest.raises(RetryEvidenceError, match="identity|invalid"):
        classify([run(sha=override.get("sha", SHA), **special)])


def test_missing_or_unbounded_inventory_and_duplicate_ids_fail_closed():
    with pytest.raises(RetryEvidenceError, match="incomplete"):
        classify_retry_pr_head({}, source_sha=SHA, repository=REPO, workflow_file=WF)
    with pytest.raises(RetryEvidenceError, match="incomplete"):
        classify([run()], total=0)
    with pytest.raises(RetryEvidenceError, match="mismatch"):
        classify([run(), run()])
    with pytest.raises(RetryEvidenceError, match="incomplete"):
        classify([run(i) for i in range(1, 22)])
    with pytest.raises(RetryEvidenceError, match="empty"):
        classify([], total=5)


def test_cli_prints_only_bounded_marker_no_log_data(monkeypatch, capsys):
    payload = {"total_count": 1, "workflow_runs": [run()]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main(["--source-sha", SHA, "--repository", REPO, "--workflow-file", WF]) == 0
    out = capsys.readouterr()
    assert out.out == "FAILED 101\n"
    assert out.err == ""

    monkeypatch.setattr(sys, "stdin", io.StringIO("{bad"))
    assert main(["--source-sha", SHA, "--repository", REPO, "--workflow-file", WF]) == 2
    out = capsys.readouterr()
    assert "RETRY_VALIDATION_EVIDENCE_INVALID" in out.err
    assert "bad" not in out.err


def test_trusted_workflow_rejects_exact_failed_no_work_and_preserves_original_pr():
    text = Path(".github/workflows/direct-worker-core.yml").read_text(encoding="utf-8")
    evidence = text.split("      - name: Collect retry validation evidence", 1)[1].split(
        "      - name: Set up source Node toolchain", 1
    )[0]
    assert "head_sha=$START_HEAD&event=pull_request" in evidence
    assert "python -m wonjae_dispatcher_runner.retry_validation_evidence" in evidence
    assert "RETRY_VALIDATION_STATE=$state" in evidence
    assert "RETRY_VALIDATION_RUN_ID=$run_id" in evidence
    assert "FAILURE_LOG=$failure_log" in evidence
    assert "RETRY_VALIDATION_LOG_UNAVAILABLE" in evidence
    assert "|| true" not in evidence
    guard = text.split("      - name: Reject retry NO_WORK with unresolved", 1)[1].split(
        "      - name: Report no-work result", 1
    )[0]
    assert "steps.codex.outputs.status == 'NO_WORK'" in guard
    assert "RETRY_NO_WORK_UNRESOLVED_CANONICAL_CI" in guard
    assert "exit 1" in guard
    assert "gh pr merge" not in guard and "gh pr create" not in guard
    failure = text.split("      - name: Record Direct Worker failure on open PR", 1)[1]
    assert 'CODEX_RESULT_STATUS: ${{ steps.codex.outputs.status }}' in failure
    assert "failed_validation_run_id" in failure
    assert "gh pr comment" in failure


def test_retry_prompt_explicitly_includes_canonical_failure_not_only_local_smoke():
    route = DirectWorkerRoute(
        project="EXAMPLE", repository=REPO, branch="cloud-dev-v1",
        startup_entry="AGENTS.md", project_rules="PROJECT_RULES.md",
        canonical_docs=("docs/NEXT_WORK.md",),
        validation_workflow_path=".github/workflows/" + WF,
        validation_workflow_name="Product Validation",
        runner_repository="owner/runner", runner_workflow=".github/workflows/direct-worker.yml",
        state_path=".wonjae/execution-state.yaml", handoff_path=None, handoff=None,
    )
    state = ProductExecutionState(
        project="EXAMPLE", path=".wonjae/execution-state.yaml",
        next_action=ExecutionAction(
            type="DECISION_REQUIRED", title="Later LIVE gate", source_task_id=None,
            owner_action="Live evidence later", source_scope=(), gate=None,
        ),
        after_source_success=None,
    )
    prompt = _prompt(
        route, "retry", SHA, "direct/EXAMPLE-100",
        "AssertionError: Immutable source: tests/old-contract.mjs", state,
    )
    assert "failed canonical validation on this exact existing PR head" in prompt
    assert "returning NO_WORK while that CI is unresolved" in prompt
    assert "Immutable source: tests/old-contract.mjs" in prompt
    assert "do not change or re-advance it" in prompt.lower()


def failed_job(id=501, run_id=101, **override):
    return {
        "id": id,
        "run_id": run_id,
        "status": "completed",
        "conclusion": "failure",
        "runner_id": 17,
        "steps": [{"conclusion": "failure", "name": "Focused source contracts"}],
        **override,
    }


def test_exact_failed_job_fallback_accepts_one_started_failed_job_only():
    skipped = [
        {"id": i, "run_id": 101, "status": "completed",
         "conclusion": "skipped", "runner_id": 0, "steps": []}
        for i in (502, 503, 504)
    ]
    evidence = {"total_count": 4, "jobs": [*skipped, failed_job()]}
    assert exact_failed_job_id(evidence, run_id=101) == 501


@pytest.mark.parametrize("case", [
    {"jobs": [failed_job(run_id=102)], "total_count": 1},
    {"jobs": [failed_job(id=0)], "total_count": 1},
    {"jobs": [failed_job(id=True)], "total_count": 1},
    {"jobs": [failed_job(), failed_job()], "total_count": 2},
    {"jobs": [failed_job(), failed_job(id=505)], "total_count": 2},
    {"jobs": [failed_job(steps=[])], "total_count": 1},
    {"jobs": [failed_job(runner_id=0)], "total_count": 1},
    {"jobs": [failed_job(status="queued", conclusion=None)], "total_count": 1},
    {"jobs": [failed_job()], "total_count": 2},
    {"jobs": [], "total_count": 0},
    {"jobs": [failed_job()], "total_count": 101},
])
def test_failed_job_fallback_rejects_drift_ambiguity_and_partial_inventory(case):
    with pytest.raises(RetryEvidenceError):
        exact_failed_job_id(case, run_id=101)


def test_failed_job_cli_emits_id_only_and_rejects_malformed_identity(monkeypatch, capsys):
    payload = {"total_count": 1, "jobs": [failed_job()]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main(["--failed-job-run-id", "101"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "501\n"
    assert captured.err == ""

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert main(["--failed-job-run-id", "999"]) == 2
    captured = capsys.readouterr()
    assert "RETRY_VALIDATION_EVIDENCE_INVALID" in captured.err
    assert "501" not in captured.err


def test_workflow_reads_exact_failed_job_log_if_run_log_is_empty():
    source = Path(".github/workflows/direct-worker-core.yml").read_text()
    evidence = source.split("      - name: Collect retry validation evidence", 1)[1].split(
        "      - name: Set up source Node toolchain", 1
    )[0]
    assert 'if [ ! -s "$failure_log" ]; then' in evidence
    assert "actions/runs/$run_id/jobs?filter=latest&per_page=100" in evidence
    assert '--failed-job-run-id "$run_id"' in evidence
    assert "actions/jobs/$job_id/logs" in evidence
    assert '--job "$job_id" --log' in evidence
    assert 'bytes="$(wc -c < "$failure_log")"' in evidence
    assert '"$bytes" -gt 2000000' in evidence
    assert "RETRY_VALIDATION_LOG_UNAVAILABLE" in evidence
    assert 'echo "FAILURE_LOG=$failure_log" >> "$GITHUB_ENV"' in evidence
    assert "|| true" not in evidence
    assert "gh pr merge" not in evidence
