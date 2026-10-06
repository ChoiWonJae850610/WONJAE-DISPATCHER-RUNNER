from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

ALLOWED_PROJECTS = {"WAFL", "CLASSMO", "ESC", "MUVEL"}


class ReconciliationError(RuntimeError):
    """Raised when a reconciliation finalizer claim is not trustworthy."""


@dataclass(frozen=True)
class ReconciliationClaim:
    project: str
    repository: str
    branch: str
    validation_workflow_file: str
    product_pr: int
    expected_head: str


def _sha(value: object) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(
        ch not in "0123456789abcdef" for ch in value
    ):
        raise ReconciliationError("expected_head must be a lowercase 40-character SHA")
    return value


def load_claim(registry_path: Path, project: str, body: str) -> ReconciliationClaim:
    if project not in ALLOWED_PROJECTS:
        raise ReconciliationError("project is not allowed for reconciliation")
    try:
        request = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ReconciliationError("reconciliation issue body must be JSON") from exc
    if not isinstance(request, dict) or set(request) != {
        "schema_version",
        "kind",
        "project",
        "product_pr",
        "expected_head",
    }:
        raise ReconciliationError("reconciliation issue JSON shape is invalid")
    if request.get("schema_version") != 1 or request.get("kind") != "product_reconciliation":
        raise ReconciliationError("reconciliation issue identity is invalid")
    if request.get("project") != project:
        raise ReconciliationError("reconciliation project mismatch")
    product_pr = request.get("product_pr")
    if not isinstance(product_pr, int) or product_pr < 1:
        raise ReconciliationError("product_pr is invalid")

    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    projects = registry.get("projects") if isinstance(registry, dict) else None
    value = projects.get(project) if isinstance(projects, dict) else None
    if not isinstance(value, dict) or value.get("status") != "active":
        raise ReconciliationError("project is not active")
    execution = value.get("execution")
    if not isinstance(execution, dict) or execution.get("mode") != "direct_worker":
        raise ReconciliationError("project is not a Direct Worker route")

    repository = value.get("repository")
    branch = value.get("branch")
    ci = value.get("ci")
    validation = ci.get("validation") if isinstance(ci, dict) else None
    workflow_path = validation.get("path") if isinstance(validation, dict) else None
    if not isinstance(repository, str) or "/" not in repository:
        raise ReconciliationError("registered repository is invalid")
    if not isinstance(branch, str) or not branch:
        raise ReconciliationError("registered branch is invalid")
    if not isinstance(workflow_path, str) or not workflow_path.startswith(
        ".github/workflows/"
    ):
        raise ReconciliationError("registered validation workflow is invalid")

    return ReconciliationClaim(
        project=project,
        repository=repository,
        branch=branch,
        validation_workflow_file=workflow_path.rsplit("/", 1)[-1],
        product_pr=product_pr,
        expected_head=_sha(request.get("expected_head")),
    )


def path_allowed(project: str, path: str) -> bool:
    if path == ".wonjae/execution-state.yaml":
        return True
    if path == "PROJECT_RULES.md":
        return True
    if path.startswith("docs/"):
        return True
    if project == "WAFL" and path == "supabase/stage4/README.md":
        return True
    return False
