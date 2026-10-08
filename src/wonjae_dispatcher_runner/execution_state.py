from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ACTION_TYPES = {"SOURCE_READY", "MANUAL_QA", "PROVIDER_GATE", "DECISION_REQUIRED", "NONE"}
MANUAL_QA_MODES = {"owner_checkout"}


class ExecutionStateError(RuntimeError):
    """Raised when a product execution-state file violates the trusted contract."""


@dataclass(frozen=True)
class ManualQaContract:
    mode: str
    required_paths: tuple[str, ...]


@dataclass(frozen=True)
class NpmPreparation:
    workspace: str
    packages: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionAction:
    type: str
    title: str
    source_task_id: str | None
    owner_action: str | None
    source_scope: tuple[str, ...]
    gate: str | None
    manual_qa: ManualQaContract | None = None
    npm_preparation: NpmPreparation | None = None


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


def _parse_manual_qa(value: object, label: str) -> ManualQaContract:
    if not isinstance(value, dict):
        raise ExecutionStateError(f"{label}.manual_qa must be a mapping")
    mode = value.get("mode")
    required_paths = value.get("required_paths")
    if mode not in MANUAL_QA_MODES:
        raise ExecutionStateError(f"{label}.manual_qa.mode is invalid")
    if not isinstance(required_paths, list) or not required_paths or not all(
        isinstance(item, str) and item.strip() for item in required_paths
    ):
        raise ExecutionStateError(f"{label}.manual_qa.required_paths is invalid")
    normalized: list[str] = []
    for item in required_paths:
        path = item.strip()
        _safe_relative(path)
        normalized.append(path)
    if len(set(normalized)) != len(normalized):
        raise ExecutionStateError(f"{label}.manual_qa.required_paths contains duplicates")
    return ManualQaContract(mode=mode, required_paths=tuple(normalized))


def _parse_action(value: object, label: str) -> ExecutionAction:
    if not isinstance(value, dict):
        raise ExecutionStateError(f"{label} must be a mapping")
    action_type = value.get("type")
    title = value.get("title")
    source_task_id = value.get("source_task_id")
    owner_action = value.get("owner_action")
    source_scope = value.get("source_scope", [])
    gate = value.get("gate")
    manual_qa_value = value.get("manual_qa")
    npm_value = value.get("npm_preparation")

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
    if action_type != "MANUAL_QA" and manual_qa_value is not None:
        raise ExecutionStateError(f"{label}.manual_qa is valid only for MANUAL_QA")
    if action_type == "MANUAL_QA" and label == "after_source_success" and manual_qa_value is None:
        raise ExecutionStateError(
            "after_source_success MANUAL_QA requires a manual_qa actionability contract"
        )
    manual_qa = (
        _parse_manual_qa(manual_qa_value, label)
        if manual_qa_value is not None
        else None
    )
    npm_preparation = None
    if npm_value is not None:
        if action_type != "SOURCE_READY" or not isinstance(npm_value, dict):
            raise ExecutionStateError(f"{label}.npm_preparation requires SOURCE_READY mapping")
        if set(npm_value) != {"workspace", "packages"}:
            raise ExecutionStateError(f"{label}.npm_preparation fields are invalid")
        workspace = npm_value.get("workspace")
        packages = npm_value.get("packages")
        if not isinstance(workspace, str) or workspace != workspace.strip():
            raise ExecutionStateError(f"{label}.npm_preparation.workspace is invalid")
        _safe_relative(workspace)
        if workspace == "." or workspace.startswith("."):
            raise ExecutionStateError(f"{label}.npm_preparation.workspace is invalid")
        pattern = r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*@\d+\.\d+\.\d+"
        if not isinstance(packages, list) or not packages or len(packages) > 20 or not all(
            isinstance(item, str) and re.fullmatch(pattern, item) for item in packages
        ) or len(set(packages)) != len(packages):
            raise ExecutionStateError(f"{label}.npm_preparation requires exact npm versions")
        npm_preparation = NpmPreparation(workspace, tuple(packages))

    return ExecutionAction(
        type=action_type,
        title=title.strip(),
        source_task_id=source_task_id.strip() if isinstance(source_task_id, str) else None,
        owner_action=owner_action.strip() if isinstance(owner_action, str) else None,
        source_scope=tuple(item.strip() for item in source_scope),
        gate=gate.strip() if isinstance(gate, str) else None,
        manual_qa=manual_qa,
        npm_preparation=npm_preparation,
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


def _verify_manual_qa_actionability(
    repo_path: Path,
    action: ExecutionAction,
    changed_paths: tuple[str, ...],
) -> None:
    if action.type != "MANUAL_QA":
        return
    contract = action.manual_qa
    if contract is None:
        raise ExecutionStateError("MANUAL_QA transition is missing actionability contract")
    if contract.mode != "owner_checkout":
        raise ExecutionStateError("MANUAL_QA transition mode is not directly actionable")
    changed = set(changed_paths)
    for relative in contract.required_paths:
        path = repo_path / relative
        if path.is_symlink() or not path.is_file():
            raise ExecutionStateError(
                f"MANUAL_QA required execution path is not a regular file: {relative}"
            )
        if relative not in changed:
            raise ExecutionStateError(
                f"MANUAL_QA required execution path was not changed by this source task: {relative}"
            )


def advance_product_execution_state(
    repo_path: Path,
    state_path: str,
    project: str,
    changed_paths: tuple[str, ...] = (),
) -> ProductExecutionState:
    state = load_product_execution_state(repo_path, state_path, project)
    if state.next_action.type != "SOURCE_READY":
        raise ExecutionStateError("only SOURCE_READY execution state may advance on source success")
    if state.after_source_success is None:
        raise ExecutionStateError("SOURCE_READY execution state has no success transition")
    _verify_manual_qa_actionability(
        repo_path,
        state.after_source_success,
        changed_paths,
    )

    path = repo_path / state_path
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["next_action"] = payload["after_source_success"]
    payload["after_source_success"] = None
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return load_product_execution_state(repo_path, state_path, project)
