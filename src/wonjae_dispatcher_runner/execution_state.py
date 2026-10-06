from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ACTION_TYPES = {"SOURCE_READY", "MANUAL_QA", "PROVIDER_GATE", "DECISION_REQUIRED", "NONE"}


class ExecutionStateError(RuntimeError):
    """Raised when a product execution-state file violates the trusted contract."""


@dataclass(frozen=True)
class ExecutionAction:
    type: str
    title: str
    source_task_id: str | None
    owner_action: str | None
    source_scope: tuple[str, ...]
    gate: str | None


@dataclass(frozen=True)
class ProductExecutionState:
    project: str
    path: str
    next_action: ExecutionAction
    after_source_success: ExecutionAction | None


def _safe_relative(path: str) -> None:
    value = Path(path)
    if value.is_absolute() or ".." in value.parts or not path.strip():
        raise ExecutionStateError("execution state path is unsafe")


def _parse_action(value: object, label: str) -> ExecutionAction:
    if not isinstance(value, dict):
        raise ExecutionStateError(f"{label} must be a mapping")
    action_type = value.get("type")
    title = value.get("title")
    source_task_id = value.get("source_task_id")
    owner_action = value.get("owner_action")
    source_scope = value.get("source_scope", [])
    gate = value.get("gate")

    if action_type not in ACTION_TYPES:
        raise ExecutionStateError(f"{label}.type is invalid")
    if not isinstance(title, str) or not title.strip():
        raise ExecutionStateError(f"{label}.title is invalid")
    if source_task_id is not None and (
        not isinstance(source_task_id, str) or not source_task_id.strip()
    ):
        raise ExecutionStateError(f"{label}.source_task_id is invalid")
    if owner_action is not None and (
        not isinstance(owner_action, str) or not owner_action.strip()
    ):
        raise ExecutionStateError(f"{label}.owner_action is invalid")
    if gate is not None and (not isinstance(gate, str) or not gate.strip()):
        raise ExecutionStateError(f"{label}.gate is invalid")
    if not isinstance(source_scope, list) or not all(
        isinstance(item, str) and item.strip() for item in source_scope
    ):
        raise ExecutionStateError(f"{label}.source_scope is invalid")
    if action_type == "SOURCE_READY" and not source_scope:
        raise ExecutionStateError(f"{label} SOURCE_READY requires source_scope")

    return ExecutionAction(
        type=action_type,
        title=title.strip(),
        source_task_id=source_task_id.strip() if isinstance(source_task_id, str) else None,
        owner_action=owner_action.strip() if isinstance(owner_action, str) else None,
        source_scope=tuple(item.strip() for item in source_scope),
        gate=gate.strip() if isinstance(gate, str) else None,
    )


def load_product_execution_state(
    repo_path: Path,
    state_path: str,
    project: str,
) -> ProductExecutionState:
    _safe_relative(state_path)
    path = repo_path / state_path
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExecutionStateError("registered product execution state is missing") from exc
    except yaml.YAMLError as exc:
        raise ExecutionStateError("product execution state YAML is invalid") from exc

    if not isinstance(payload, dict):
        raise ExecutionStateError("product execution state must be a mapping")
    if payload.get("schema_version") != 1:
        raise ExecutionStateError("product execution state schema_version must be 1")
    if payload.get("project") != project:
        raise ExecutionStateError("product execution state project mismatch")

    next_action = _parse_action(payload.get("next_action"), "next_action")
    after_value = payload.get("after_source_success")
    after = None if after_value is None else _parse_action(after_value, "after_source_success")
    if next_action.type == "SOURCE_READY" and after is None:
        raise ExecutionStateError(
            "SOURCE_READY product execution state requires after_source_success"
        )

    return ProductExecutionState(
        project=project,
        path=state_path,
        next_action=next_action,
        after_source_success=after,
    )


def advance_product_execution_state(
    repo_path: Path,
    state_path: str,
    project: str,
) -> ProductExecutionState:
    state = load_product_execution_state(repo_path, state_path, project)
    if state.next_action.type != "SOURCE_READY":
        raise ExecutionStateError("only SOURCE_READY execution state may advance on source success")
    if state.after_source_success is None:
        raise ExecutionStateError("SOURCE_READY execution state has no success transition")

    path = repo_path / state_path
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["next_action"] = payload["after_source_success"]
    payload["after_source_success"] = None
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return load_product_execution_state(repo_path, state_path, project)
