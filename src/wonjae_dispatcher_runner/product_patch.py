from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .guards import require_sha

DIFF_HEADER_RE = re.compile(r"^diff --git a/([^\s]+) b/([^\s]+)$", re.MULTILINE)
MAX_PATCH_CHARS = 240_000


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
    scope: tuple[str, ...]
    exclusions: tuple[str, ...]
    completion_conditions: tuple[str, ...]


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


def _string_list(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ProductPilotError(f"{key} must be a non-empty string list")
    return tuple(item.strip() for item in value)


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
        allowed_paths=_string_list(payload, "allowed_paths"),
        scope=_string_list(payload, "scope"),
        exclusions=_string_list(payload, "exclusions"),
        completion_conditions=_string_list(payload, "completion_conditions"),
    )


def patch_paths(patch: str) -> tuple[str, ...]:
    if not patch.strip():
        raise ProductPilotError("Codex returned an empty patch")
    if len(patch) > MAX_PATCH_CHARS:
        raise ProductPilotError("Codex patch exceeded the bounded size limit")
    prohibited = (
        "GIT binary patch",
        "Binary files ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
        "Subproject commit ",
    )
    if any(marker in patch for marker in prohibited):
        raise ProductPilotError("binary, rename, copy, or submodule patches are prohibited")

    pairs = DIFF_HEADER_RE.findall(patch)
    if not pairs:
        raise ProductPilotError("Codex response did not contain a unified Git diff")

    paths: list[str] = []
    for old_path, new_path in pairs:
        if old_path != new_path:
            raise ProductPilotError("path-changing diffs are prohibited")
        path = Path(new_path)
        if path.is_absolute() or ".." in path.parts:
            raise ProductPilotError("unsafe patch path")
        normalized = path.as_posix()
        if normalized not in paths:
            paths.append(normalized)
    return tuple(paths)


def validate_patch_scope(patch: str, work_order: ProductWorkOrder) -> tuple[str, ...]:
    paths = patch_paths(patch)
    allowed = set(work_order.allowed_paths)
    disallowed = [path for path in paths if path not in allowed]
    if disallowed:
        raise ProductPilotError("Codex patch included a path outside the work order")
    return paths


def apply_patch(repo_path: Path, patch: str, work_order: ProductWorkOrder) -> tuple[str, ...]:
    expected_paths = validate_patch_scope(patch, work_order)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".patch",
        delete=False,
    ) as handle:
        handle.write(patch)
        patch_path = Path(handle.name)

    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "apply",
                "--check",
                "--whitespace=error-all",
                str(patch_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "apply", "--whitespace=error-all", str(patch_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
    finally:
        patch_path.unlink(missing_ok=True)

    actual = tuple(changed_paths(repo_path))
    if not actual or set(actual) != set(expected_paths):
        raise ProductPilotError("applied worktree paths did not match the validated patch")
    return actual


def _bounded_prompt(work_order: ProductWorkOrder, control_sha: str) -> str:
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
            "Read every required file from the checkout before deciding the patch:",
            *[f"- {item}" for item in work_order.required_reads],
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
            "Do not modify files directly. Do not commit, push, create PRs, use network services, "
            "or access credentials. Inspect the read-only checkout, then return one unified Git "
            "diff against the exact source base. The patch must implement the task completely "
            "within the allowed paths, include focused tests, preserve truthful evidence "
            "boundaries, and avoid unsupported product capabilities.",
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

    for relative in work_order.required_reads:
        if not (repo_path / relative).is_file():
            raise ProductPilotError(f"required product read is missing: {relative}")

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
            "patch": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["patch", "summary"],
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
            _bounded_prompt(work_order, control_sha),
            approval_mode=ApprovalMode.deny_all,
            output_schema=output_schema,
            sandbox=Sandbox.read_only,
        )

    status = str(getattr(result.status, "value", result.status)).lower()
    if status != "completed" or result.error is not None:
        raise ProductPilotError("Codex product turn did not complete successfully")

    try:
        payload = json.loads(result.final_response or "")
    except json.JSONDecodeError as exc:
        raise ProductPilotError("Codex product response was not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"patch", "summary"}:
        raise ProductPilotError("Codex product response shape was invalid")
    patch = payload.get("patch")
    if not isinstance(patch, str):
        raise ProductPilotError("Codex product patch was missing")

    if git_head(repo_path) != work_order.source_base_sha or changed_paths(repo_path):
        raise ProductPilotError("product repository changed during the read-only Codex turn")

    applied = apply_patch(repo_path, patch, work_order)
    return work_order, applied
