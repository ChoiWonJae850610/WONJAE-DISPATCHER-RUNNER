from pathlib import Path

import pytest
import yaml

from wonjae_dispatcher_runner.execution_state import (
    ExecutionStateError,
    advance_product_execution_state,
    load_product_execution_state,
)


def write_state(root: Path, action_type: str = "SOURCE_READY") -> Path:
    path = root / ".wonjae" / "execution-state.yaml"
    path.parent.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "project": "WAFL",
        "next_action": {
            "type": action_type,
            "title": "Prepare source hardening",
            "source_task_id": "WAFL-SOURCE-001" if action_type == "SOURCE_READY" else None,
            "owner_action": None,
            "source_scope": (
                ["Change the bounded source migration."]
                if action_type == "SOURCE_READY"
                else []
            ),
            "gate": None,
        },
        "after_source_success": {
            "type": "PROVIDER_GATE",
            "title": "Apply the validated migration",
            "source_task_id": None,
            "owner_action": "Approve the non-Production provider apply.",
            "source_scope": [],
            "gate": "separate_nonproduction_provider_stage",
        } if action_type == "SOURCE_READY" else None,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_load_source_ready_product_state(tmp_path: Path) -> None:
    write_state(tmp_path)
    state = load_product_execution_state(
        tmp_path, ".wonjae/execution-state.yaml", "WAFL"
    )
    assert state.next_action.type == "SOURCE_READY"
    assert state.next_action.source_task_id == "WAFL-SOURCE-001"
    assert state.after_source_success is not None
    assert state.after_source_success.type == "PROVIDER_GATE"


def test_source_ready_requires_declared_success_transition(tmp_path: Path) -> None:
    path = write_state(tmp_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["after_source_success"] = None
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ExecutionStateError, match="after_source_success"):
        load_product_execution_state(
            tmp_path, ".wonjae/execution-state.yaml", "WAFL"
        )


def test_trusted_advance_replaces_next_action(tmp_path: Path) -> None:
    path = write_state(tmp_path)
    state = advance_product_execution_state(
        tmp_path, ".wonjae/execution-state.yaml", "WAFL"
    )
    assert state.next_action.type == "PROVIDER_GATE"
    assert state.after_source_success is None
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert payload["next_action"]["type"] == "PROVIDER_GATE"
    assert payload["after_source_success"] is None


def test_manual_state_does_not_require_transition(tmp_path: Path) -> None:
    write_state(tmp_path, "MANUAL_QA")
    state = load_product_execution_state(
        tmp_path, ".wonjae/execution-state.yaml", "WAFL"
    )
    assert state.next_action.type == "MANUAL_QA"
    assert state.after_source_success is None
