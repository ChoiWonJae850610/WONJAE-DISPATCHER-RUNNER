"""Mock-only fixed-identity regression; never performs a live model call."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
import yaml

from scripts.wafl_013_codex_diagnostic import (
    CONTROL,
    FAILED_RUN,
    OWNER,
    RECOVERY_PR,
    RECOVERY_PR_HEAD,
    RUN_NAME,
    RUNNER,
    TASK,
    WAFL,
    DiagnosticGateError,
    verify_owner_gate,
)
from wonjae_dispatcher_runner.codex_safe_diagnostic import MAX_PROBE_SECONDS, PROBE_PROMPT

SOURCE = "5fa4d39f0c5ddad923eebc60b8b58c9391955331"
CONTROL_SHA = "b" * 40
RUNNER_SHA = "a" * 40


def as_content(value):
    return {
        "encoding": "base64",
        "content": base64.b64encode(yaml.safe_dump(value).encode()).decode(),
    }


def evidence():
    return {
        f"repos/{RUNNER}/branches/main": {"commit": {"sha": RUNNER_SHA}},
        f"repos/{CONTROL}/branches/main": {"commit": {"sha": CONTROL_SHA}},
        f"repos/{WAFL}/branches/cloud-dev-v1": {"commit": {"sha": SOURCE}},
        f"repos/{CONTROL}/contents/PROJECTS.yaml?ref={CONTROL_SHA}": as_content({
            "projects": {"WAFL": {
                "status": "active", "repository": WAFL, "branch": "cloud-dev-v1",
                "execution": {"mode": "direct_worker", "runner_repository": RUNNER,
                              "state_path": ".wonjae/execution-state.yaml"},
            }},
        }),
        f"repos/{WAFL}/contents/.wonjae/execution-state.yaml?ref={SOURCE}": as_content({
            "next_action": {"type": "SOURCE_READY", "source_task_id": TASK, "gate": None},
        }),
        f"repos/{RUNNER}/actions/runs/{FAILED_RUN}": {
            "id": FAILED_RUN, "status": "completed", "conclusion": "failure",
            "event": "workflow_dispatch", "display_title": "Direct Worker WAFL next",
            "path": ".github/workflows/direct-worker.yml",
        },
        f"repos/{WAFL}/pulls/{RECOVERY_PR}": {
            "number": RECOVERY_PR, "head": {"sha": RECOVERY_PR_HEAD},
            "base": {"ref": "cloud-dev-v1"},
        },
        f"repos/{RUNNER}/actions/workflows/direct-worker-codex-diagnostic.yml/"
        "runs?event=workflow_dispatch&per_page=100": {"workflow_runs": []},
        f"repos/{RUNNER}/actions/workflows/direct-worker.yml/"
        "runs?status=in_progress&per_page=100": {"workflow_runs": []},
    }


def check(source=evidence, *, actor=OWNER, head=SOURCE):
    obj = source() if callable(source) else source
    verify_owner_gate(
        obj.__getitem__, source_sha=head, control_sha=CONTROL_SHA,
        runner_sha=RUNNER_SHA, run_id=123, actor=actor,
    )


def test_valid_fixed_historical_evidence():
    check()


@pytest.mark.parametrize(("path", "patch", "code"), [
    (f"repos/{WAFL}/branches/cloud-dev-v1",
     {"commit": {"sha": "c" * 40}}, "WAFL_HEAD_CHANGED"),
    (f"repos/{CONTROL}/branches/main",
     {"commit": {"sha": "c" * 40}}, "CONTROL_MAIN_CHANGED"),
    (f"repos/{RUNNER}/actions/runs/{FAILED_RUN}",
     {"conclusion": "success"}, "ORIGINAL_RUN_NOT_FAILED"),
    (f"repos/{WAFL}/pulls/{RECOVERY_PR}",
     {"head": {"sha": "a" * 40}}, "RECOVERY_IDENTITY_CHANGED"),
])
def test_stale_identity_blocks_before_codex(path, patch, code):
    data = evidence()
    data[path].update(patch)
    with pytest.raises(DiagnosticGateError, match=code):
        check(data)


def test_wrong_stage_and_non_owner_block():
    data = evidence()
    path = f"repos/{WAFL}/contents/.wonjae/execution-state.yaml?ref={SOURCE}"
    data[path] = as_content({"next_action": {
        "type": "PROVIDER_GATE", "source_task_id": TASK, "gate": "danger",
    }})
    with pytest.raises(DiagnosticGateError, match="PRODUCT_STAGE_CHANGED"):
        check(data)
    with pytest.raises(DiagnosticGateError, match="INPUT_IDENTITY"):
        check(actor="other")


def test_duplicate_and_running_writer_block():
    data = evidence()
    path = f"repos/{RUNNER}/actions/workflows/direct-worker-codex-diagnostic.yml/" \
           "runs?event=workflow_dispatch&per_page=100"
    data[path]["workflow_runs"] = [{
        "id": 122, "event": "workflow_dispatch", "head_branch": "main",
        "display_title": RUN_NAME + SOURCE,
        "path": ".github/workflows/direct-worker-codex-diagnostic.yml",
    }]
    with pytest.raises(DiagnosticGateError, match="ALREADY_ATTEMPTED"):
        check(data)
    data = evidence()
    path = f"repos/{RUNNER}/actions/workflows/direct-worker.yml/" \
           "runs?status=in_progress&per_page=100"
    data[path]["workflow_runs"] = [{"display_title": "Direct Worker WAFL next"}]
    with pytest.raises(DiagnosticGateError, match="SOURCE_WORK_ACTIVE"):
        check(data)


def test_workflow_has_only_trusted_manual_trigger_and_no_secret_write():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/direct-worker-codex-diagnostic.yml").read_text()
    workflow = yaml.safe_load(text)
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert set(trigger["workflow_dispatch"]["inputs"]) == {"source_sha", "control_sha"}
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["synthetic-probe"]
    assert job["timeout-minutes"] <= 5
    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "direct-worker"]
    assert "github.actor == 'ChoiWonJae850610'" in job["if"]
    assert text.count("scripts/wafl_013_codex_diagnostic.py") == 1
    for forbidden in (
        "gh secret set", "Rotate credential", "git push", "git merge",
        "direct_worker_authenticated_smoke.py", "pull_request_target",
        "wrangler deploy", "eas build",
    ):
        assert forbidden not in text
    script = (root / "scripts/wafl_013_codex_diagnostic.py").read_text()
    assert "gh secret set" not in script
    assert "MAX_PROBE_SECONDS" in script
    assert "ApprovalMode.deny_all" not in script  # existing observer owns approval safety
    assert MAX_PROBE_SECONDS == 120
    assert PROBE_PROMPT.startswith("Reply with the single word OK")
