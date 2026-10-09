"""Read-only contract checks for proposed trusted automated recovery workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

from wonjae_dispatcher_runner.failure_recovery_document import RecoveryPreparationError

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/prepare_failed_source_recovery.py"
spec = importlib.util.spec_from_file_location("prepare_failed_source_recovery", SCRIPT)
assert spec and spec.loader
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def failed_run():
    return {
        "id": 37938489783,
        "name": "Direct Worker",
        "display_title": "Direct Worker WAFL next",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "path": ".github/workflows/direct-worker.yml@refs/heads/main",
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": "failure",
        "run_attempt": 1,
        "actor": {"login": "ChoiWonJae850610"},
        "repository": {"full_name": prep.RUNNER},
    }


def failed_jobs():
    return {"jobs": [{
        "id": 113846525951, "name": "wafl / direct-worker",
        "status": "completed", "conclusion": "failure", "runner_id": 31,
        "steps": [
            {"name": "Checkout trusted runner", "conclusion": "success"},
            {"name": "Run isolated Codex Direct Worker", "conclusion": "failure"},
        ],
    }]}


def registry():
    return {
        "schema_version": 1,
        "projects": {
            "WAFL": {
                "status": "active", "repository": "ChoiWonJae850610/WAFL",
                "branch": "cloud-dev-v1",
                "execution": {"mode": "direct_worker",
                              "state_path": ".wonjae/execution-state.yaml"},
            },
        },
    }


def test_owner_triggered_failed_run_and_exact_job_are_sufficient_to_inspect():
    prep.checked_run(failed_run(), "WAFL", "a" * 40)
    assert prep.checked_failure_job(failed_jobs(), "WAFL") == 113846525951
    assert prep.checked_registry(registry(), "WAFL")["branch"] == "cloud-dev-v1"


@pytest.mark.parametrize("field,value", [
    ("name", "Provider Gate Approval"),
    ("display_title", "Direct Worker CLASSMO next"),
    ("event", "pull_request"), ("head_branch", "cloud-dev-v1"),
    ("head_sha", "b" * 40), ("conclusion", "success"),
    ("status", "in_progress"), ("run_attempt", 2),
    ("repository", {"full_name": "ChoiWonJae850610/WAFL"}),
    ("actor", {"login": "someone-else"}),
])
def test_untrusted_or_stale_run_identity_is_rejected(field, value):
    run = failed_run()
    run[field] = value
    with pytest.raises(RecoveryPreparationError):
        prep.checked_run(run, "WAFL", "a" * 40)


@pytest.mark.parametrize("mutate", [
    lambda jobs: jobs["jobs"][0].update({"runner_id": 0}),
    lambda jobs: jobs["jobs"][0].update({"status": "in_progress"}),
    lambda jobs: jobs["jobs"][0]["steps"][1].update({"name": "EAS build"}),
    lambda jobs: jobs["jobs"][0]["steps"].append({
        "name": "Publish", "conclusion": "failure"}),
    lambda jobs: jobs["jobs"][0].update({"conclusion": "success"}),
    lambda jobs: jobs.update({"jobs": jobs["jobs"] * 21}),
])
def test_non_exact_failure_stage_fails_closed(mutate):
    jobs = failed_jobs()
    mutate(jobs)
    with pytest.raises(RecoveryPreparationError):
        prep.checked_failure_job(jobs, "WAFL")


@pytest.mark.parametrize("corrupt", [
    lambda r: r["projects"]["WAFL"].update({"repository": "example/WAFL"}),
    lambda r: r["projects"]["WAFL"].update({"status": "inactive"}),
    lambda r: r["projects"]["WAFL"].update({"branch": "main"}),
    lambda r: r["projects"]["WAFL"].update({"branch": "../another"}),
    lambda r: r["projects"]["WAFL"]["execution"].update({"mode": "dispatcher_v2"}),
    lambda r: r["projects"]["WAFL"]["execution"].update({"state_path": "/secrets"}),
])
def test_unknown_or_protected_product_registry_rejected(corrupt):
    value = registry()
    corrupt(value)
    with pytest.raises(RecoveryPreparationError):
        prep.checked_registry(value, "WAFL")


def test_recovery_workflow_is_not_a_new_source_or_merge_executor():
    top = yaml.safe_load((ROOT / ".github/workflows/automatic-source-recovery-docs.yml")
                         .read_text(encoding="utf-8"))
    trigger = top.get("on", top.get(True))
    assert set(trigger) == {"workflow_run"}
    assert trigger["workflow_run"]["workflows"] == ["Direct Worker"]
    assert trigger["workflow_run"]["types"] == ["completed"]
    assert top["permissions"] == {"contents": "read", "actions": "read"}
    assert set(top["jobs"]) == {"wafl", "classmo", "esc", "muvel"}
    for project in ("WAFL", "CLASSMO", "ESC", "MUVEL"):
        job = top["jobs"][project.lower()]
        assert job["with"]["project"] == project
        assert "workflow_run.conclusion == 'failure'" in job["if"]
        assert "workflow_run.event == 'workflow_dispatch'" in job["if"]
        assert job["secrets"]["product_write_token"] == (
            "${{ secrets." + project + "_WRITE_TOKEN }}"
        )
        assert job["uses"] == "./.github/workflows/automatic-source-recovery-docs-core.yml"
    core = yaml.safe_load(
        (ROOT / ".github/workflows/automatic-source-recovery-docs-core.yml")
        .read_text(encoding="utf-8")
    )
    assert core["permissions"] == {"contents": "read", "actions": "read"}
    assert core["jobs"]["prepare"]["runs-on"] == [
        "self-hosted", "Linux", "X64", "direct-worker",
    ]
    scripts = str(core)
    assert "prepare_failed_source_recovery.py" in scripts
    assert "dispatches" not in scripts and "eas build" not in scripts
    assert "pull_request_target" not in scripts
    assert "on:" in (ROOT / ".github/workflows/automatic-source-recovery-docs.yml"
                     ).read_text(encoding="utf-8")
