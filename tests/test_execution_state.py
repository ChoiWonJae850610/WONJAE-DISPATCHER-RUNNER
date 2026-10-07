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


def manual_after_source(path: Path, *, with_contract: bool = True) -> None:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["after_source_success"] = {
        "type": "MANUAL_QA",
        "title": "Verify the integrated source on the Owner device",
        "source_task_id": None,
        "owner_action": "Run the exact integrated source through the declared Owner checkout path.",
        "source_scope": [],
        "gate": "physical_owner_checkout",
    }
    if with_contract:
        payload["after_source_success"]["manual_qa"] = {
            "mode": "owner_checkout",
            "required_paths": ["qa/manual-owner-entrypoint.txt"],
        }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_source_ready_rejects_manual_qa_without_actionability_contract(tmp_path: Path) -> None:
    path = write_state(tmp_path)
    manual_after_source(path, with_contract=False)
    with pytest.raises(ExecutionStateError, match="actionability contract"):
        load_product_execution_state(
            tmp_path, ".wonjae/execution-state.yaml", "WAFL"
        )


def test_manual_qa_transition_requires_real_changed_execution_paths(tmp_path: Path) -> None:
    path = write_state(tmp_path)
    manual_after_source(path)

    state = load_product_execution_state(
        tmp_path, ".wonjae/execution-state.yaml", "WAFL"
    )
    assert state.after_source_success is not None
    assert state.after_source_success.manual_qa is not None
    assert state.after_source_success.manual_qa.mode == "owner_checkout"
    assert state.after_source_success.manual_qa.required_paths == (
        "qa/manual-owner-entrypoint.txt",
    )

    with pytest.raises(ExecutionStateError, match="not a regular file"):
        advance_product_execution_state(
            tmp_path,
            ".wonjae/execution-state.yaml",
            "WAFL",
            changed_paths=("qa/manual-owner-entrypoint.txt",),
        )

    entry = tmp_path / "qa" / "manual-owner-entrypoint.txt"
    entry.parent.mkdir(parents=True)
    entry.write_text("owner executable surface\n", encoding="utf-8")

    with pytest.raises(ExecutionStateError, match="was not changed"):
        advance_product_execution_state(
            tmp_path,
            ".wonjae/execution-state.yaml",
            "WAFL",
            changed_paths=("other.txt",),
        )

    advanced = advance_product_execution_state(
        tmp_path,
        ".wonjae/execution-state.yaml",
        "WAFL",
        changed_paths=("qa/manual-owner-entrypoint.txt",),
    )
    assert advanced.next_action.type == "MANUAL_QA"
    assert advanced.next_action.manual_qa is not None


def test_manual_qa_transition_rejects_non_direct_mode(tmp_path: Path) -> None:
    path = write_state(tmp_path)
    manual_after_source(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["after_source_success"]["manual_qa"]["mode"] = "provider_required"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ExecutionStateError, match="manual_qa.mode"):
        load_product_execution_state(
            tmp_path, ".wonjae/execution-state.yaml", "WAFL"
        )
