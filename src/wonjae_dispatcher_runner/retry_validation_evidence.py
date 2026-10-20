"""Classify exact PR-head validation evidence before an Owner-initiated source retry.

Pure GitHub-response validation only: never dispatch a workflow, change a product,
substitute a local smoke test for canonical CI, or print untrusted log content.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW_FILE = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")


class RetryEvidenceError(ValueError):
    """Malformed or mismatched GitHub validation evidence is not clearance."""


def classify_retry_pr_head(
    payload: object, *, source_sha: str, repository: str, workflow_file: str
) -> tuple[str, int]:
    """Return (NONE | PASS | FAILED | BLOCKED, exact latest run ID or 0).

    The trusted caller fetches the registered workflow's pull_request runs
    filtered by the existing source-PR HEAD (never the active base HEAD).
    Never select an older failure if the latest exact run is already successful.
    """
    if not SHA.fullmatch(source_sha) or not repository or not WORKFLOW_FILE.fullmatch(
        workflow_file
    ):
        raise RetryEvidenceError("invalid exact retry source identity")
    if not isinstance(payload, dict):
        raise RetryEvidenceError("invalid validation response")
    rows = payload.get("workflow_runs")
    total = payload.get("total_count")
    if (
        not isinstance(rows, list)
        or len(rows) > 20
        or type(total) is not int
        or total < len(rows)
    ):
        raise RetryEvidenceError("incomplete validation run inventory")
    if not rows:
        if total:
            raise RetryEvidenceError("empty nonzero validation run inventory")
        return "NONE", 0

    expected_path = ".github/workflows/" + workflow_file
    seen: set[int] = set()
    for run in rows:
        if not isinstance(run, dict):
            raise RetryEvidenceError("malformed validation run")
        run_id = run.get("id")
        repo = run.get("repository")
        head_repo = run.get("head_repository")
        path = run.get("path")
        if (
            type(run_id) is not int
            or run_id <= 0
            or run_id in seen
            or run.get("head_sha") != source_sha
            or run.get("event") != "pull_request"
            or not isinstance(repo, dict)
            or repo.get("full_name") != repository
            or (head_repo is not None and (
                not isinstance(head_repo, dict)
                or head_repo.get("full_name") != repository
            ))
            or not isinstance(path, str)
            or path.split("@", 1)[0] != expected_path
        ):
            raise RetryEvidenceError("validation run identity mismatch")
        seen.add(run_id)

    latest = max(rows, key=lambda run: run["id"])
    status = latest.get("status")
    conclusion = latest.get("conclusion")
    if status in {"queued", "in_progress", "requested", "waiting", "pending"}:
        if conclusion is not None:
            raise RetryEvidenceError("nonterminal validation has a conclusion")
        return "BLOCKED", latest["id"]
    if status != "completed" or not isinstance(conclusion, str):
        raise RetryEvidenceError("invalid terminal validation state")
    if conclusion == "success":
        return "PASS", latest["id"]
    if conclusion == "failure":
        return "FAILED", latest["id"]
    # Cancelled, skipped, timed out, action_required, etc. cannot prove success.
    return "BLOCKED", latest["id"]


def exact_failed_job_id(payload: object, *, run_id: int) -> int:
    """Select the one started/failed job of an already verified exact failed run.

    A job log may only be read from this run. Multiple failures, pagination or
    malformed responses are ambiguous and intentionally stop the source retry.
    """
    if type(run_id) is not int or run_id <= 0 or not isinstance(payload, dict):
        raise RetryEvidenceError("invalid failed-run job identity")
    rows, total = payload.get("jobs"), payload.get("total_count")
    if (
        not isinstance(rows, list)
        or type(total) is not int
        or total < 1
        or total > 100
        or len(rows) != total
    ):
        raise RetryEvidenceError("incomplete exact failed-run job inventory")

    ids: set[int] = set()
    failed: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RetryEvidenceError("invalid job result")
        job_id, actual_run_id = row.get("id"), row.get("run_id")
        if (
            type(job_id) is not int
            or job_id <= 0
            or job_id in ids
            or actual_run_id != run_id
            or type(actual_run_id) is not int
        ):
            raise RetryEvidenceError("failed job run identity mismatch")
        ids.add(job_id)
        if row.get("status") == "completed" and row.get("conclusion") == "failure":
            steps = row.get("steps")
            runner_id = row.get("runner_id")
            if (
                type(runner_id) is not int
                or runner_id <= 0
                or not isinstance(steps, list)
                or not any(isinstance(x, dict) and x.get("conclusion") == "failure"
                           for x in steps)
            ):
                raise RetryEvidenceError("failed job has no started failed step")
            failed.append(job_id)
    if len(failed) != 1:
        raise RetryEvidenceError("exact failed job is missing or ambiguous")
    return failed[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha")
    parser.add_argument("--repository")
    parser.add_argument("--workflow-file")
    parser.add_argument("--failed-job-run-id", type=int)
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if args.failed_job_run_id is not None:
            if any((args.source_sha, args.repository, args.workflow_file)):
                raise RetryEvidenceError("ambiguous retry evidence CLI mode")
            print(exact_failed_job_id(payload, run_id=args.failed_job_run_id))
        else:
            if not all((args.source_sha, args.repository, args.workflow_file)):
                raise RetryEvidenceError("missing exact source identity")
            status, run_id = classify_retry_pr_head(
                payload, source_sha=args.source_sha,
                repository=args.repository, workflow_file=args.workflow_file,
            )
            print(f"{status} {run_id}")
    except (RetryEvidenceError, ValueError, TypeError, json.JSONDecodeError):
        print("RETRY_VALIDATION_EVIDENCE_INVALID", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
