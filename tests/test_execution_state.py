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


@pytest.mark.parametrize("packages", [
    ["synthetic@latest"], ["synthetic@^1.0.0"], ["file:../private"],
    ["--registry=example.invalid"], ["synthetic@1.0.0", "synthetic@1.0.0"], [],
])
def test_npm_preparation_requires_bounded_exact_versions(tmp_path, packages):
    path = write_state(tmp_path)
    payload = yaml.safe_load(path.read_text())
    payload["next_action"]["npm_preparation"] = {"workspace": "apps/client", "packages": packages}
    path.write_text(yaml.safe_dump(payload))
    with pytest.raises(ExecutionStateError, match="exact npm versions"):
        load_product_execution_state(tmp_path, ".wonjae/execution-state.yaml", "WAFL")


def test_npm_preparation_is_task_bound_and_disappears_on_transition(tmp_path):
    path = write_state(tmp_path)
    payload = yaml.safe_load(path.read_text())
    payload["next_action"]["npm_preparation"] = {
        "workspace": "apps/client", "packages": ["synthetic@1.0.0"],
    }
    path.write_text(yaml.safe_dump(payload))
    state = load_product_execution_state(tmp_path, ".wonjae/execution-state.yaml", "WAFL")
    assert state.next_action.npm_preparation.packages == ("synthetic@1.0.0",)
    state = advance_product_execution_state(tmp_path, ".wonjae/execution-state.yaml", "WAFL")
    assert state.next_action.npm_preparation is None


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


def queued_source(title: str, task_id: str) -> dict[str, object]:
    return {
        "type": "SOURCE_READY", "title": title, "source_task_id": task_id,
        "source_scope": [f"Implement bounded {title}."],
        "gate": None, "owner_action": None,
    }


def queued_gate() -> dict[str, object]:
    return {
        "type": "PROVIDER_GATE", "title": "Owner-authorized preview validation",
        "source_task_id": None, "source_scope": [],
        "gate": "preview_ios_build", "owner_action": "Approve existing Preview executor.",
    }


def setup_continuous_plan(path: Path) -> None:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["after_source_success"] = queued_source("Stage B", "WAFL-SOURCE-002")
    value["source_success_queue"] = [
        queued_source("Stage C", "WAFL-SOURCE-003"),
        queued_gate(),
    ]
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_trusted_multiple_stage_plan_reaches_gate_without_new_doc_pr(tmp_path):
    path = write_state(tmp_path)
    setup_continuous_plan(path)

    first = load_product_execution_state(
        tmp_path, ".wonjae/execution-state.yaml", "WAFL",
    )
    assert first.next_action.source_task_id == "WAFL-SOURCE-001"
    assert first.after_source_success.source_task_id == "WAFL-SOURCE-002"
    assert len(first.source_success_queue) == 2

    second = advance_product_execution_state(
        tmp_path, ".wonjae/execution-state.yaml", "WAFL",
    )
    assert second.next_action.source_task_id == "WAFL-SOURCE-002"
    assert second.after_source_success.source_task_id == "WAFL-SOURCE-003"
    assert len(second.source_success_queue) == 1

    third = advance_product_execution_state(
        tmp_path, ".wonjae/execution-state.yaml", "WAFL",
    )
    assert third.next_action.source_task_id == "WAFL-SOURCE-003"
    assert third.after_source_success.type == "PROVIDER_GATE"
    assert not third.source_success_queue

    gated = advance_product_execution_state(
        tmp_path, ".wonjae/execution-state.yaml", "WAFL",
    )
    assert gated.next_action.type == "PROVIDER_GATE"
    assert gated.after_source_success is None
    assert gated.source_success_queue == ()
    with pytest.raises(ExecutionStateError, match="only SOURCE_READY"):
        advance_product_execution_state(
            tmp_path, ".wonjae/execution-state.yaml", "WAFL",
        )


@pytest.mark.parametrize(
    "queue,successor,problem",
    [
        ([], queued_source("B", "WAFL-SOURCE-002"), "terminating"),
        ([queued_source("C", "WAFL-SOURCE-003")],
         queued_source("B", "WAFL-SOURCE-002"), "terminate"),
        ([queued_gate(), queued_gate()], queued_source("B", "WAFL-SOURCE-002"), "chain"),
        ([queued_gate()], queued_gate(), "requires SOURCE_READY"),
        ([queued_gate()] * 9, queued_source("B", "WAFL-SOURCE-002"), "bounded"),
        ([queued_gate()], queued_source("B", "WAFL-SOURCE-001"), "distinct"),
        ([queued_gate()], queued_source("B", ""), "distinct"),
    ],
)
def test_continuous_source_queue_fail_closed(tmp_path, queue, successor, problem):
    path = write_state(tmp_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["after_source_success"] = successor
    payload["source_success_queue"] = queue
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ExecutionStateError, match=problem):
        load_product_execution_state(tmp_path, ".wonjae/execution-state.yaml", "WAFL")


def test_gate_must_not_enqueue_new_source_automatically(tmp_path):
    path = write_state(tmp_path, "PROVIDER_GATE")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["source_success_queue"] = [
        queued_source("Another source", "WAFL-SOURCE-002"), queued_gate(),
    ]
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ExecutionStateError, match="requires SOURCE_READY"):
        load_product_execution_state(tmp_path, ".wonjae/execution-state.yaml", "WAFL")


def test_invalid_manual_qa_final_stage_never_skips_actionability(tmp_path):
    path = write_state(tmp_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["after_source_success"] = queued_source("Stage B", "WAFL-SOURCE-002")
    payload["source_success_queue"] = [
        {"type": "MANUAL_QA", "title": "Review signed app", "source_scope": []},
    ]
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ExecutionStateError, match="actionability contract"):
        load_product_execution_state(tmp_path, ".wonjae/execution-state.yaml", "WAFL")


def test_backward_compatible_single_source_gate_plan_does_not_add_queue(tmp_path):
    path = write_state(tmp_path)
    advance_product_execution_state(tmp_path, ".wonjae/execution-state.yaml", "WAFL")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "source_success_queue" not in payload
    assert payload["after_source_success"] is None
