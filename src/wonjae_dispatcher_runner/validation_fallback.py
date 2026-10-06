"""Shared fail-closed static validation policy; no provider authority is created."""

from __future__ import annotations

from datetime import datetime
from typing import Any

ALLOWED_PROJECTS = {"WAFL", "CLASSMO", "ESC", "MUVEL"}
RUNNER_REPOSITORY = "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER"
EXTERNAL_WORKFLOW_PATH = ".github/workflows/external-product-validation.yml"


def exact_canonical(run: dict[str, Any], *, repository: str, workflow_path: str,
                    source_sha: str, event: str, run_id: int) -> bool:
    return (
        isinstance(run, dict) and type(run_id) is int and run_id > 0
        and run.get("id") == run_id
        and (run.get("repository") or {}).get("full_name") == repository
        and (run.get("head_repository") or {}).get("full_name") == repository
        and run.get("path") == workflow_path
        and run.get("head_sha") == source_sha
        and event in {"push", "pull_request"} and run.get("event") == event
    )


def is_runner_allocation_failure(run: dict[str, Any], jobs: list[dict[str, Any]]) -> bool:
    if (not isinstance(run, dict) or run.get("status") != "completed"
            or run.get("conclusion") != "failure" or not isinstance(jobs, list) or not jobs):
        return False
    return all(
        isinstance(job, dict) and type(job.get("runner_id")) is int
        and job["runner_id"] == 0 and isinstance(job.get("steps"), list)
        and not job["steps"] and job.get("run_id") == run.get("id")
        for job in jobs
    )


def external_validation_titles(project: str, source_sha: str) -> tuple[str, str]:
    if project not in ALLOWED_PROJECTS:
        raise ValueError("project is not eligible for external validation fallback")
    if len(source_sha) != 40 or any(ch not in "0123456789abcdef" for ch in source_sha):
        raise ValueError("source_sha must be a lowercase 40-character SHA")
    return (f"[EXTERNAL-VALIDATE][{project}] {source_sha}",
            f"External Product Validation {project} {source_sha}")


def timestamp(value: object) -> float:
    if not isinstance(value, str) or not value:
        return -1
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else -1
    except ValueError:
        return -1


def matching_external_run(run: dict[str, Any], *, project: str, source_sha: str,
                          not_before: str, workflow_id: int) -> bool:
    issue_title, dispatch_title = external_validation_titles(project, source_sha)
    floor, created = timestamp(not_before), timestamp(run.get("created_at"))
    return (
        floor >= 0 and created > floor
        and type(run.get("id")) is int and run["id"] > 0
        and type(workflow_id) is int and workflow_id > 0
        and run.get("workflow_id") == workflow_id
        and (run.get("repository") or {}).get("full_name") == RUNNER_REPOSITORY
        and (run.get("head_repository") or {}).get("full_name") == RUNNER_REPOSITORY
        and run.get("head_branch") == "main"
        and isinstance(run.get("head_sha"), str) and len(run["head_sha"]) == 40
        and run.get("path") == EXTERNAL_WORKFLOW_PATH
        and ((run.get("event") == "issues" and run.get("display_title") == issue_title)
             or (run.get("event") == "workflow_dispatch"
                 and run.get("display_title") == dispatch_title))
    )


def matching_external_success(runs: list[dict[str, Any]], *, project: str, source_sha: str,
                              not_before: str, workflow_id: int) -> dict[str, Any] | None:
    matches = [run for run in runs if isinstance(run, dict)
               and matching_external_run(run, project=project, source_sha=source_sha,
                                         not_before=not_before, workflow_id=workflow_id)
               and run.get("status") == "completed" and run.get("conclusion") == "success"]
    return max(matches, key=lambda item: item["id"], default=None)


def matching_dispatched_external_run(runs: list[dict[str, Any]], *, project: str,
                                     source_sha: str, minimum_run_id: int,
                                     not_before: str, workflow_id: int) -> dict[str, Any] | None:
    matches = [run for run in runs if isinstance(run, dict)
               and matching_external_run(run, project=project, source_sha=source_sha,
                                         not_before=not_before, workflow_id=workflow_id)
               and run.get("event") == "workflow_dispatch" and run["id"] > minimum_run_id]
    if len(matches) > 1:
        raise ValueError("ambiguous external validation dispatch identity")
    return matches[0] if matches else None
