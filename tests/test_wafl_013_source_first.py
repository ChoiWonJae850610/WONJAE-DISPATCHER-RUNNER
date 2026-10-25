"""Offline prompt regression for WAFL 013: source first, narrow checks, no bypass."""
from __future__ import annotations

from dataclasses import replace

from test_direct_worker import registry

from wonjae_dispatcher_runner.direct_worker import _prompt, load_direct_worker_route
from wonjae_dispatcher_runner.execution_state import ExecutionAction, ProductExecutionState


def _render(tmp_path, task: str, *, project: str = "WAFL", command: str = "next") -> str:
    baseline = load_direct_worker_route(registry(tmp_path), "ESC")
    route = replace(
        baseline, project=project,
        repository="ChoiWonJae850610/WAFL" if project == "WAFL"
        else "ChoiWonJae850610/CLASSMO",
    )
    state = ProductExecutionState(
        project=project,
        path=".wonjae/execution-state.yaml",
        next_action=ExecutionAction(
            type="SOURCE_READY",
            title="Already approved image source task",
            source_task_id=task,
            owner_action=None,
            source_scope=("Edit only signed image source and affected tests.",),
            gate=None,
        ),
        after_source_success=ExecutionAction(
            type="DECISION_REQUIRED", title="Provider/device handoff",
            source_task_id=None, owner_action="Owner confirms next gate.",
            source_scope=(), gate=None,
        ),
    )
    return _prompt(route, command, "a" * 40, "direct/WAFL-test", "", state)


def test_wafl_013_no_pre_edit_typecheck_gate_and_real_source_required(tmp_path):
    prompt = _render(tmp_path, "WAFL-IMAGE-LOCAL-DERIVATIVES-013-001")
    assert "IMPLEMENT the already authorized source changes first" in prompt
    assert "pre-edit admission gate" in prompt
    assert "missing expo/tsconfig.base" in prompt
    assert "do not return MANUAL_REQUIRED solely" in prompt.lower()
    assert "Return CHANGED only if actual authorized source files changed" in prompt
    assert "NOT_RUN" in prompt
    assert "never claim local TypeScript PASS" in prompt


def test_canonical_ci_and_safety_boundaries_remain_authoritative(tmp_path):
    prompt = _render(tmp_path, "WAFL-IMAGE-LOCAL-DERIVATIVES-013-001")
    assert "mandatory exact PR-head and integrated-SHA gate" in prompt
    assert "its real failures must be fixed" in prompt
    assert "Preserve all signed R2, Label" in prompt
    assert "MANUAL_REQUIRED" in prompt
    assert "Never advance or rewrite the protected product execution state" in prompt
    assert "Do not perform provider, deployment, Production, credential" in prompt
    assert "The trusted Runner owns Git branch preparation" in prompt


def test_no_other_project_or_source_task_receives_waiver(tmp_path):
    ordinary_wafl = _render(tmp_path, "WAFL-OTHER-014")
    classmo = _render(tmp_path, "WAFL-IMAGE-LOCAL-DERIVATIVES-013-001", project="CLASSMO")
    retry = _render(tmp_path, "WAFL-IMAGE-LOCAL-DERIVATIVES-013-001", command="retry")
    for prompt in (ordinary_wafl, classmo, retry):
        assert "a missing expo/tsconfig.base" not in prompt
        assert "pre-edit admission gate" not in prompt
        assert "GitHub Actions remains the exact-SHA validation authority" in prompt
