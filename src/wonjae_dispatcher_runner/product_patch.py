from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .guards import require_sha

MAX_EDIT_COUNT = 48
MAX_TEXT_CHARS = 80_000
MAX_CONTEXT_CHARS = 600_000
PROHIBITED_FAILURE_MARKERS = (
    "blocked pending checkout inspection",
    "do not integrate this marker",
    "execution sandbox failed",
    "bwrap:",
    "failed rtm_newaddr",
)


class ProductPilotError(RuntimeError):
    """Raised when a product pilot violates its bounded work order."""


@dataclass(frozen=True)
class ProductWorkOrder:
    task_id: str
    project: str
    repository: str
    target_branch: str
    source_base_sha: str
    title: str
    integration_authorized: bool
    validation_workflow_path: str
    required_reads: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    required_changed_paths: tuple[str, ...]
    scope: tuple[str, ...]
    exclusions: tuple[str, ...]
    completion_conditions: tuple[str, ...]


@dataclass(frozen=True)
class ProductEdit:
    path: str
    operation: str
    old_text: str
    new_text: str


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return result.stdout


def git_head(repo_path: Path) -> str:
    return require_sha(_git(repo_path, "rev-parse", "HEAD").strip())


def changed_paths(repo_path: Path) -> list[str]:
    output = _git(repo_path, "status", "--porcelain=v1", "--untracked-files=all")
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            raise ProductPilotError("unexpected git status entry")
        path = line[3:]
        if " -> " in path:
            raise ProductPilotError("renames are not allowed in the product pilot")
        paths.append(path)
    return paths


def _string_list(
    payload: dict[str, object],
    key: str,
    *,
    required: bool = True,
) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (required and not value) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ProductPilotError(f"{key} must be a string list")
    return tuple(item.strip() for item in value)


def _validate_relative_paths(paths: tuple[str, ...], label: str) -> None:
    for relative in paths:
        path_value = Path(relative)
        if path_value.is_absolute() or ".." in path_value.parts:
            raise ProductPilotError(f"{label} contains an unsafe path")


def load_work_order(path: Path) -> ProductWorkOrder:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ProductPilotError("unsupported product work-order schema")

    def required_string(key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProductPilotError(f"{key} must be a non-empty string")
        return value.strip()

    source_base_sha = require_sha(required_string("source_base_sha"))
    integration = payload.get("integration_authorized")
    if not isinstance(integration, bool):
        raise ProductPilotError("integration_authorized must be boolean")

    allowed_paths = _string_list(payload, "allowed_paths")
    required_changed_paths = _string_list(
        payload,
        "required_changed_paths",
        required=False,
    )
    _validate_relative_paths(allowed_paths, "allowed_paths")
    _validate_relative_paths(required_changed_paths, "required_changed_paths")
    if any(path not in set(allowed_paths) for path in required_changed_paths):
        raise ProductPilotError("required_changed_paths must be a subset of allowed_paths")

    return ProductWorkOrder(
        task_id=required_string("task_id"),
        project=required_string("project"),
        repository=required_string("repository"),
        target_branch=required_string("target_branch"),
        source_base_sha=source_base_sha,
        title=required_string("title"),
        integration_authorized=integration,
        validation_workflow_path=required_string("validation_workflow_path"),
        required_reads=_string_list(payload, "required_reads"),
        allowed_paths=allowed_paths,
        required_changed_paths=required_changed_paths,
        scope=_string_list(payload, "scope"),
        exclusions=_string_list(payload, "exclusions"),
        completion_conditions=_string_list(payload, "completion_conditions"),
    )


def _reject_failure_marker(text: str) -> None:
    normalized = text.casefold()
    if any(marker in normalized for marker in PROHIBITED_FAILURE_MARKERS):
        raise ProductPilotError("Codex edit contained a prohibited failure marker")


def parse_edit_plan(response: str, work_order: ProductWorkOrder) -> tuple[ProductEdit, ...]:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ProductPilotError("Codex product response was not valid JSON") from exc

    if not isinstance(payload, dict) or set(payload) != {"edits", "summary"}:
        raise ProductPilotError("Codex product response shape was invalid")
    summary = payload.get("summary")
    edits = payload.get("edits")
    if not isinstance(summary, str) or not summary.strip():
        raise ProductPilotError("Codex product summary was missing")
    if not isinstance(edits, list) or not edits:
        raise ProductPilotError("Codex returned an empty edit plan")
    if len(edits) > MAX_EDIT_COUNT:
        raise ProductPilotError("Codex edit plan exceeded the bounded edit count")

    allowed = set(work_order.allowed_paths)
    parsed: list[ProductEdit] = []
    created_paths: set[str] = set()
    for raw in edits:
        if not isinstance(raw, dict) or set(raw) != {
            "path",
            "operation",
            "old_text",
            "new_text",
        }:
            raise ProductPilotError("Codex edit entry shape was invalid")
        path = raw.get("path")
        operation = raw.get("operation")
        old_text = raw.get("old_text")
        new_text = raw.get("new_text")
        if not all(isinstance(value, str) for value in (path, operation, old_text, new_text)):
            raise ProductPilotError("Codex edit entry values must be strings")
        assert isinstance(path, str)
        assert isinstance(operation, str)
        assert isinstance(old_text, str)
        assert isinstance(new_text, str)

        if path not in allowed:
            raise ProductPilotError("Codex edit included a path outside the work order")
        path_value = Path(path)
        if path_value.is_absolute() or ".." in path_value.parts:
            raise ProductPilotError("Codex edit included an unsafe path")
        if operation not in {"replace", "create"}:
            raise ProductPilotError("Codex edit operation was invalid")
        if "\x00" in old_text or "\x00" in new_text:
            raise ProductPilotError("Codex edit contained a NUL byte")
        if len(old_text) > MAX_TEXT_CHARS or len(new_text) > MAX_TEXT_CHARS:
            raise ProductPilotError("Codex edit text exceeded the bounded size limit")
        if not new_text:
            raise ProductPilotError(
                "Codex edit may not delete a file or replace with empty content"
            )
        _reject_failure_marker(new_text)
        if operation == "create":
            if old_text:
                raise ProductPilotError("create edits must use an empty old_text")
            if path in created_paths:
                raise ProductPilotError("duplicate create edit for one path")
            created_paths.add(path)
        elif not old_text:
            raise ProductPilotError("replace edits require a non-empty old_text")
        if operation == "replace" and old_text == new_text:
            continue

        parsed.append(
            ProductEdit(
                path=path,
                operation=operation,
                old_text=old_text,
                new_text=new_text,
            )
        )

    if not parsed:
        raise ProductPilotError("Codex edit plan contained no effective edits")
    return tuple(parsed)


def apply_edit_plan(
    repo_path: Path,
    edits: tuple[ProductEdit, ...],
    work_order: ProductWorkOrder,
) -> tuple[str, ...]:
    allowed = set(work_order.allowed_paths)
    contents: dict[str, str | None] = {}
    original: dict[str, str | None] = {}

    for edit in edits:
        if edit.path not in allowed:
            raise ProductPilotError("edit plan path escaped the work order")
        if edit.path not in contents:
            target = repo_path / edit.path
            value = target.read_text(encoding="utf-8") if target.is_file() else None
            contents[edit.path] = value
            original[edit.path] = value

        current = contents[edit.path]
        if edit.operation == "create":
            if current is not None:
                raise ProductPilotError("create edit targeted an existing file")
            contents[edit.path] = edit.new_text
            continue

        if current is None:
            raise ProductPilotError("replace edit targeted a missing file")
        occurrences = current.count(edit.old_text)
        if occurrences != 1:
            raise ProductPilotError(
                "replace edit old_text did not match exactly once in the current file"
            )
        contents[edit.path] = current.replace(edit.old_text, edit.new_text, 1)

    changed: list[str] = []
    for path, value in contents.items():
        if value is None or value == original[path]:
            continue
        target = repo_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
        changed.append(path)

    if not changed:
        raise ProductPilotError("Codex edit plan produced no repository changes")

    actual = tuple(changed_paths(repo_path))
    if set(actual) != set(changed):
        raise ProductPilotError("worktree paths did not match the validated edit plan")
    if any(path not in allowed for path in actual):
        raise ProductPilotError("worktree contains a path outside the work order")
    missing_required = [
        path for path in work_order.required_changed_paths if path not in set(actual)
    ]
    if missing_required:
        raise ProductPilotError("worktree omitted required changed paths")
    return actual


def _required_context(repo_path: Path, work_order: ProductWorkOrder) -> str:
    chunks: list[str] = []
    total = 0
    for relative in work_order.required_reads:
        path = repo_path / relative
        if not path.is_file():
            raise ProductPilotError(f"required product read is missing: {relative}")
        content = path.read_text(encoding="utf-8")
        if "\x00" in content:
            raise ProductPilotError("required product context contained a NUL byte")
        chunk = f"\n--- BEGIN {relative} ---\n{content}\n--- END {relative} ---\n"
        total += len(chunk)
        if total > MAX_CONTEXT_CHARS:
            raise ProductPilotError("required product context exceeded the bounded size limit")
        chunks.append(chunk)
    return "".join(chunks)


def _bounded_prompt(
    work_order: ProductWorkOrder,
    control_sha: str,
    context: str,
) -> str:
    return "\n".join(
        [
            "You are implementing one Owner-authorized bounded product task.",
            f"Control SHA: {require_sha(control_sha)}",
            f"Task-ID: {work_order.task_id}",
            f"Project: {work_order.project}",
            f"Repository: {work_order.repository}",
            f"Target branch: {work_order.target_branch}",
            f"Exact source base: {work_order.source_base_sha}",
            "",
            "All required repository files are supplied verbatim below.",
            "Do not call shell, filesystem, network, or external tools.",
            "Use only the supplied context to decide the edits.",
            "",
            "Scope:",
            *[f"- {item}" for item in work_order.scope],
            "",
            "Exclusions:",
            *[f"- {item}" for item in work_order.exclusions],
            "",
            "Completion conditions:",
            *[f"- {item}" for item in work_order.completion_conditions],
            "",
            "Allowed changed paths:",
            *[f"- {item}" for item in work_order.allowed_paths],
            "",
            "Required changed paths:",
            *[f"- {item}" for item in work_order.required_changed_paths],
            "",
            "Return a structured edit plan only. For an existing file use operation=replace with "
            "an exact old_text snippet copied verbatim from the supplied context and the intended "
            "new_text; choose the smallest snippet that matches exactly once. For a new file use "
            "operation=create, old_text='', and the complete new file content. Return at least one "
            "effective edit. Do not return placeholders, blocked markers, sandbox-error markers, "
            "or commentary instead of implementation. Implement the task completely within the "
            "allowed paths, include focused tests, preserve truthful evidence boundaries, and do "
            "not invent capabilities that the current repository cannot safely support.",
            "",
            "REQUIRED REPOSITORY CONTEXT:",
            context,
        ]
    )


def generate_and_apply_product_patch(
    repo_path: Path,
    work_order_path: Path,
    control_sha: str,
    codex_home: Path,
) -> tuple[ProductWorkOrder, tuple[str, ...]]:
    work_order = load_work_order(work_order_path)
    if git_head(repo_path) != work_order.source_base_sha:
        raise ProductPilotError("product checkout does not match the work-order source base")
    if changed_paths(repo_path):
        raise ProductPilotError("product checkout must be clean before Codex")

    context = _required_context(repo_path, work_order)

    config = CodexConfig(
        cwd=str(repo_path),
        env={
            "CODEX_HOME": str(codex_home),
            "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
        },
    )
    output_schema = {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_EDIT_COUNT,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "enum": list(work_order.allowed_paths),
                        },
                        "operation": {
                            "type": "string",
                            "enum": ["replace", "create"],
                        },
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string", "minLength": 1},
                    },
                    "required": ["path", "operation", "old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string", "minLength": 1},
        },
        "required": ["edits", "summary"],
        "additionalProperties": False,
    }

    with Codex(config=config) as codex:
        account = codex.account(refresh_token=False)
        if account.account is None:
            raise ProductPilotError("Codex account session is missing")
        thread = codex.thread_start(
            approval_mode=ApprovalMode.deny_all,
            cwd=str(repo_path),
            ephemeral=True,
            sandbox=Sandbox.read_only,
            config={
                "history": {"persistence": "none"},
                "allow_login_shell": False,
            },
        )
        result = thread.run(
            _bounded_prompt(work_order, control_sha, context),
            approval_mode=ApprovalMode.deny_all,
            output_schema=output_schema,
            sandbox=Sandbox.read_only,
        )

    status = str(getattr(result.status, "value", result.status)).lower()
    if status != "completed" or result.error is not None:
        raise ProductPilotError("Codex product turn did not complete successfully")

    edits = parse_edit_plan(result.final_response or "", work_order)

    if git_head(repo_path) != work_order.source_base_sha or changed_paths(repo_path):
        raise ProductPilotError("product repository changed during the read-only Codex turn")

    applied = apply_edit_plan(repo_path, edits, work_order)
    return work_order, applied
