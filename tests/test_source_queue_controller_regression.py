"""Synthetic A -> B E2E through the actual controller, without product writes."""

import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.source_queue import queue_state

PATH = Path(__file__).resolve().parents[1] / "scripts/source_queue.py"
SPEC = importlib.util.spec_from_file_location("queue_controller_regression", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CA, CB, SA, IA = "a" * 40, "b" * 40, "1" * 40, "2" * 40
TITLE = f"[PRODUCT-WAKE][DISPATCHER-V2] CLASSMO CLASSMO-QUEUE-A-001 {CA} {SA}"


@pytest.fixture
def queue(monkeypatch):
    proof = {
        "project": "CLASSMO",
        "task_id": "CLASSMO-QUEUE-A-001",
        "attempt": "2",
        "control_sha": CA,
        "source_sha": SA,
        "wake_issue": "101",
        "result": "COMPLETED",
        "source_run_id": "7001",
        "product_pr": "50",
        "integrated_sha": IA,
        "exact_integrated_sha_validation_run": "7002",
        "active_writer": "RELEASED_BY_MERGED_PR",
    }
    body = "\n".join(
        [MODULE.QUEUE_TERMINAL_HEADING, "", *(f"- {k}: `{v}`" for k, v in proof.items())]
    )
    a = {
        "number": 101,
        "title": TITLE,
        "state": "CLOSED",
        "author": {"login": "owner"},
        "comments": [{"body": body, "author": {"login": "github-actions[bot]"}}],
    }
    b = {
        "number": 102,
        "title": f"[PRODUCT-QUEUE][DISPATCHER-V2] CLASSMO CLASSMO-QUEUE-B-001 {CB} 101",
        "state": "OPEN",
        "author": {"login": "owner"},
        "comments": [],
    }
    record = {
        "schema_version": 2,
        "task_id": "CLASSMO-QUEUE-B-001",
        "project": "CLASSMO",
        "operation_type": "source_change",
        "integration_authorized": True,
        "source_base": {
            "mode": "predecessor_integrated_sha",
            "predecessor_wake_issue": 101,
            "predecessor_task_id": "CLASSMO-QUEUE-A-001",
            "predecessor_control_sha": CA,
            "predecessor_attempt": 2,
        },
    }
    issues = {101: a, 102: b}
    calls = []
    run = {
        "id": 7001,
        "repository": {"full_name": "owner/runner"},
        "event": "workflow_dispatch",
        "head_branch": "main",
        "path": ".github/workflows/classmo-product-pilot.yml",
        "display_title": TITLE,
        "actor": {"login": "github-actions[bot]"},
        "status": "in_progress",
        "conclusion": None,
    }

    def gh(command, **kwargs):
        if "/jobs?" in command[-1]:
            return {
                "jobs": [
                    {"name": "classmo-product", "status": "completed", "conclusion": "success"}
                ]
            }
        assert command[-1].endswith("/runs/7001")
        return run

    monkeypatch.setattr(MODULE, "gh_json", gh)
    monkeypatch.setattr(MODULE, "issue_view", lambda repo, n: issues[n])
    monkeypatch.setattr(
        MODULE,
        "list_issues",
        lambda repo, state: [
            issue for issue in issues.values() if state == "all" or issue["state"] == state.upper()
        ],
    )
    monkeypatch.setattr(MODULE, "read_control_for_registration", lambda _: record)
    monkeypatch.setattr(
        MODULE,
        "post_comment",
        lambda repo, n, text: issues[n]["comments"].append(
            {"body": text, "author": {"login": "github-actions[bot]"}}
        ),
    )
    monkeypatch.setattr(MODULE, "run", lambda command, **kwargs: calls.append(command) or "")
    args = Namespace(
        repository="owner/runner",
        owner="owner",
        controller_run_id="8001",
        source_run_id="7001",
        wake_title=TITLE,
        conclusion="success",
        queue_issue_number=None,
        issue_number=102,
    )
    return Namespace(a=a, b=b, calls=calls, args=args, run=run)


def test_exact_completed_explicit_handoff_dispatches_b_once(queue):
    assert MODULE.reconcile(queue.args) == 0
    assert queue_state(MODULE.issue_comments(queue.b)) == "DISPATCHED"
    assert len(queue.calls) == 1
    assert f"source_sha={IA}" in queue.calls[0]
    fields = MODULE.latest_queue_evidence(MODULE.issue_comments(queue.b)).fields
    assert fields["predecessor_source_run_id"] == "7001"
    assert fields["predecessor_attempt"] == "2"
    assert fields["predecessor_control_sha"] == CA
    assert fields["predecessor_issue"] == "101"
    assert fields["predecessor_integrated_sha"] == IA
    assert fields["successor_queue_issue"] == "102"


def test_explicit_handoff_workflow_run_and_duplicates_dispatch_once(queue):
    MODULE.reconcile(queue.args)
    queue.run.update(status="completed", conclusion="success")
    MODULE.advance(queue.args)
    MODULE.reconcile(queue.args)
    assert len(queue.calls) == 1


@pytest.mark.parametrize("result", ["FAILED", "MANUAL_REQUIRED", "CANCELLED"])
def test_non_success_predecessor_remains_queued_without_mutation(queue, result):
    queue.a["comments"][0]["body"] = queue.a["comments"][0]["body"].replace("COMPLETED", result)
    MODULE.intake(queue.args)
    assert queue_state(MODULE.issue_comments(queue.b)) == "QUEUED"
    assert queue.calls == []


def test_completed_predecessor_at_intake_dispatches_immediately(queue):
    MODULE.intake(queue.args)
    MODULE.intake(queue.args)
    assert len(queue.calls) == 1


def test_missed_event_reconcile_uses_existing_queue_issue(queue):
    queue.args.queue_issue_number = 102
    MODULE.reconcile(queue.args)
    MODULE.reconcile(queue.args)
    assert queue.b["number"] == 102
    assert len(queue.calls) == 1


def test_cancelled_successor_is_not_revived(queue):
    queue.b["comments"].append(
        {
            "body": "## Dispatcher v2 terminal evidence\n- result: `CANCELLED`",
            "author": {"login": "owner"},
        }
    )
    MODULE.reconcile(queue.args)
    assert queue.calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("active_writer", "RESIDUE"),
        ("exact_integrated_sha_validation_run", ""),
        ("wake_issue", "999"),
        ("attempt", "1"),
    ],
)
def test_incomplete_or_wrong_predecessor_proof_never_dispatches(queue, field, value):
    import re

    queue.a["comments"][0]["body"] = re.sub(
        rf"- {field}: `[^`]*`", f"- {field}: `{value}`", queue.a["comments"][0]["body"]
    )
    with pytest.raises(RuntimeError):
        MODULE.reconcile(queue.args)
    assert queue.calls == []


def test_failed_actual_source_run_cannot_advance_from_stale_completed_comment(queue):
    queue.run.update(status="completed", conclusion="failure")
    with pytest.raises(RuntimeError, match="conclusion mismatch"):
        MODULE.reconcile(queue.args)
    assert queue.calls == []


def test_untrusted_completion_comment_cannot_activate_successor(queue):
    queue.a["comments"][0]["author"]["login"] = "untrusted-contributor"
    MODULE.intake(queue.args)
    assert queue_state(MODULE.issue_comments(queue.b)) == "QUEUED"
    assert queue.calls == []


def test_ambiguous_dispatch_response_never_releases_claim_or_redispatches(queue, monkeypatch):
    def uncertain(command, **kwargs):
        queue.calls.append(command)
        raise TimeoutError("POST receipt unavailable")

    monkeypatch.setattr(MODULE, "run", uncertain)
    with pytest.raises(TimeoutError):
        MODULE.reconcile(queue.args)
    MODULE.reconcile(queue.args)
    assert queue_state(MODULE.issue_comments(queue.b)) == "DISPATCHED"
    assert len(queue.calls) == 1


def test_product_workflows_handoff_only_after_completed_writer_job():
    for file in ("classmo-product-pilot", "wafl-product-v2", "esc-product-v2", "muvel-product-v2"):
        text = Path(f".github/workflows/{file}.yml").read_text()
        assert "queue-handoff:" in text
        assert ".result == 'success'" in text
        assert "gh workflow run source-queue-controller.yml" in text
        assert 'source_run_id="$GITHUB_RUN_ID"' in text
        assert "PRODUCT_GH_TOKEN:" in text
        assert "--head-validation-run" in text
        assert "validate_product_scope.py" in text
