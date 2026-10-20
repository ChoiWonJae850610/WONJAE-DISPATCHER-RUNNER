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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-file", required=True)
    args = parser.parse_args(argv)
    try:
        status, run_id = classify_retry_pr_head(
            json.load(sys.stdin),
            source_sha=args.source_sha,
            repository=args.repository,
            workflow_file=args.workflow_file,
        )
    except (RetryEvidenceError, ValueError, TypeError, json.JSONDecodeError):
        print("RETRY_VALIDATION_EVIDENCE_INVALID", file=sys.stderr)
        return 2
    print(f"{status} {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
