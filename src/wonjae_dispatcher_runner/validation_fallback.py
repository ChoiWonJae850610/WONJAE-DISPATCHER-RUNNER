from __future__ import annotations

from datetime import datetime
from typing import Any

ALLOWED_PROJECTS = {"WAFL", "CLASSMO", "ESC", "MUVEL"}
EXTERNAL_WORKFLOW_PATH = ".github/workflows/external-product-validation.yml"


def is_runner_allocation_failure(run: dict[str, Any], jobs: list[dict[str, Any]]) -> bool:
    """True only when GitHub failed before assigning any hosted runner or starting a step."""
    if (
        not isinstance(run, dict)
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or not isinstance(jobs, list)
        or not jobs
    ):
        return False

    for job in jobs:
        if not isinstance(job, dict):
            return False
        if job.get("runner_id") != 0:
            return False
        steps = job.get("steps")
        if not isinstance(steps, list) or steps:
            return False
    return True


def external_validation_titles(project: str, source_sha: str) -> tuple[str, str]:
    if project not in ALLOWED_PROJECTS:
        raise ValueError("project is not eligible for external validation fallback")
    if len(source_sha) != 40 or any(ch not in "0123456789abcdef" for ch in source_sha):
        raise ValueError("source_sha must be a lowercase 40-character SHA")
    return (
        f"[EXTERNAL-VALIDATE][{project}] {source_sha}",
        f"External Product Validation {project} {source_sha}",
    )


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value:
        return -1
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return -1


def matching_external_success(
    runs: list[dict[str, Any]],
    *,
    project: str,
    source_sha: str,
    not_before: str,
) -> dict[str, Any] | None:
    issue_title, dispatch_title = external_validation_titles(project, source_sha)
    floor = _timestamp(not_before)
    matches: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("path", "")).split("@", 1)[0] != EXTERNAL_WORKFLOW_PATH:
            continue
        if run.get("event") not in {"issues", "workflow_dispatch"}:
            continue
        if run.get("display_title") not in {issue_title, dispatch_title}:
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        if _timestamp(run.get("created_at")) < floor:
            continue
        matches.append(run)
    return max(matches, key=lambda item: int(item.get("id") or 0), default=None)


def matching_dispatched_external_run(
    runs: list[dict[str, Any]],
    *,
    project: str,
    source_sha: str,
    minimum_run_id: int,
) -> dict[str, Any] | None:
    _, dispatch_title = external_validation_titles(project, source_sha)
    matches = [
        run
        for run in runs
        if isinstance(run, dict)
        and run.get("event") == "workflow_dispatch"
        and run.get("display_title") == dispatch_title
        and str(run.get("path", "")).split("@", 1)[0] == EXTERNAL_WORKFLOW_PATH
        and isinstance(run.get("id"), int)
        and run["id"] > minimum_run_id
    ]
    return max(matches, key=lambda item: item["id"], default=None)
