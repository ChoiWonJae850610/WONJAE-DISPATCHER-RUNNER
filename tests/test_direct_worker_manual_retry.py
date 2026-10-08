"""Offline safety regressions for MANUAL_REQUIRED and same-PR source retry."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from wonjae_dispatcher_runner.direct_worker import _prompt, load_direct_worker_route
from wonjae_dispatcher_runner.execution_state import (
    ExecutionAction,
    ProductExecutionState,
)

ROOT = Path(__file__).resolve().parents[1]


def test_manual_required_is_not_relabelled_failed_and_keeps_failed_run_evidence():
    source = (ROOT / ".github/workflows/direct-worker-core.yml").read_text()
    manual = source.split(
        "      - name: Preserve manual gate on existing Direct Worker PR", 1
    )[1].split("      - name: Wait for exact PR-head validation", 1)[0]
    failure = source.split(
        "      - name: Record Direct Worker failure on open PR", 1
    )[1].split("      - name: Cleanup exact ephemeral Direct Worker storage", 1)[0]
    assert 'printf -- \'- result: `%s`\\n\' "MANUAL_REQUIRED"' in manual
    assert "gh pr comment" in manual
    assert "exit 1" in manual  # Real gate must never claim successful GitHub Actions.
    assert "steps.codex.outputs.status != 'MANUAL_REQUIRED'" in failure
    assert 'printf -- \'- result: `%s`\\n\' "FAILED"' in failure


def test_state_advances_after_successful_retry_and_resume_only_on_changed():
    source = (ROOT / ".github/workflows/direct-worker-core.yml").read_text()
    advance = source.split("      - name: Advance product execution state on source success", 1)[1]
    advance = advance.split("      - name: Commit Direct Worker changes", 1)[0]
    assert "steps.codex.outputs.status == 'CHANGED'" in advance
    assert "inputs.command == 'next' &&" not in advance
    assert '--command "${{ inputs.command }}"' in advance


def _write_registry(root: Path) -> Path:
    path = root / "PROJECTS.yaml"
    path.write_text(
        "schema_version: 1\n"
        "projects:\n  CLASSMO:\n    status: active\n"
        "    repository: example/CLASSMO\n    branch: cloud-dev-v1\n"
        "    startup_entry: AGENTS.md\n    project_rules: PROJECT_RULES.md\n"
        "    canonical_docs:\n      - docs/NEXT_WORK.md\n"
        "    ci:\n      validation:\n        name: CLASSMO CI\n"
        "        path: .github/workflows/validate.yml\n"
        "    execution:\n      mode: direct_worker\n"
        "      source_writer_concurrency: 1\n      cross_project_parallel: true\n"
        "      runner_repository: ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER\n"
        "      runner_workflow: .github/workflows/direct-worker.yml\n"
        "      state_path: .wonjae/execution-state.yaml\n"
    )
    return path


def _write_state(repo: Path) -> Path:
    state_path = repo / ".wonjae/execution-state.yaml"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "project": "CLASSMO",
        "next_action": {
            "type": "SOURCE_READY", "title": "Repair native library dependency tree",
            "source_task_id": "CLASSMO-021-001",
            "owner_action": "Retry exactly the existing PR",
            "source_scope": ["Allow bounded root package.json override for worklets"],
            "gate": None,
        },
        "after_source_success": {
            "type": "DECISION_REQUIRED", "title": "New native Preview build approval",
            "source_task_id": "CLASSMO-021-001",
            "owner_action": "Decide new native build later",
            "source_scope": [],
            "gate": None,
        },
    }, sort_keys=False), encoding="utf-8")
    return state_path


def test_retry_prompt_reads_current_protected_task_scope(tmp_path: Path):
    reg = _write_registry(tmp_path)
    route = load_direct_worker_route(reg, "CLASSMO")
    action = ExecutionAction(
        type="SOURCE_READY",
        title="Native UI compatibility repair",
        source_task_id="CLASSMO-021-001",
        owner_action=None,
        source_scope=("Allow bounded root package.json override for worklets",),
        gate=None,
    )
    state = ProductExecutionState(
        project="CLASSMO", path=".wonjae/execution-state.yaml",
        next_action=action, after_source_success=None,
    )
    prompt = _prompt(route, "retry", "a" * 40, "direct/CLASSMO-123", "", state)
    assert "product execution state snapshot" in prompt
    assert "Allow bounded root package.json override for worklets" in prompt
    assert "For command retry" in prompt
    assert "legacy repository-document fallback" not in prompt


def test_trusted_retry_state_advance_then_idempotent_noop(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = _write_state(repo)
    (repo / "synthetic.txt").write_text("one\n", encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.email", "runner-test@example.invalid"],
        ["config", "user.name", "Runner Test"],
        ["add", "."],
        ["commit", "-m", "synthetic base"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "synthetic.txt").write_text("two\n", encoding="utf-8")
    registry = _write_registry(tmp_path)
    command = [
        sys.executable, str(ROOT / "scripts/advance_execution_state.py"),
        "--registry", str(registry),
        "--project", "CLASSMO",
        "--product-checkout", str(repo),
    ]
    env = __import__("os").environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    first = subprocess.run(
        [*command, "--command", "retry"],
        env=env, text=True, capture_output=True, check=False,
    )
    assert first.returncode == 0, first.stderr
    assert "PRODUCT_EXECUTION_STATE_ADVANCE=DECISION_REQUIRED" in first.stdout
    assert yaml.safe_load(state.read_text())["next_action"]["type"] == "DECISION_REQUIRED"
    second = subprocess.run(
        [*command, "--command", "retry"],
        env=env, text=True, capture_output=True, check=False,
    )
    assert second.returncode == 0, second.stderr
    assert "PRODUCT_EXECUTION_STATE_ADVANCE=ALREADY_ADVANCED_DECISION_REQUIRED" in second.stdout
    third = subprocess.run(
        [*command, "--command", "next"],
        env=env, text=True, capture_output=True, check=False,
    )
    assert third.returncode != 0  # New next cannot advance a terminal gate.
