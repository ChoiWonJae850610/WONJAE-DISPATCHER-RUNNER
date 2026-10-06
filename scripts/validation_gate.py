"""Require exact product validation, with a narrow hosted-runner allocation fallback."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wonjae_dispatcher_runner.source_validation import (  # noqa: E402
    SourceValidationError,
    wait_exact_validation,
)
from wonjae_dispatcher_runner.validation_fallback import (  # noqa: E402
    EXTERNAL_WORKFLOW_PATH,
    RUNNER_REPOSITORY,
    exact_canonical,
    is_runner_allocation_failure,
    matching_dispatched_external_run,
    matching_external_success,
)


class ValidationGateError(RuntimeError):
    pass


def gh_json(token: str, endpoint: str, *, timeout: int = 20) -> dict:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    output = subprocess.run(
        ["gh", "api", "--method", "GET", endpoint],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    ).stdout
    value = json.loads(output)
    if not isinstance(value, dict):
        raise ValidationGateError("GitHub response must be a mapping")
    return value


def product_run_reader(token: str, repository: str):
    def read(run_id: int, *, timeout_seconds: float):
        return gh_json(
            token,
            f"repos/{repository}/actions/runs/{run_id}",
            timeout=max(1, int(timeout_seconds)),
        )
    return read


def complete_list(token: str, endpoint: str, key: str) -> list[dict]:
    items = []
    for page in range(1, 5):
        payload = gh_json(token, f"{endpoint}&per_page=100&page={page}")
        values = payload.get(key)
        total = payload.get("total_count")
        if not isinstance(values, list) or type(total) is not int:
            raise ValidationGateError("validation list evidence is malformed")
        items.extend(values)
        if len(items) == total:
            return items
        if len(items) > total or not values:
            raise ValidationGateError("validation list evidence is incomplete")
    raise ValidationGateError("validation list evidence exceeds the complete-read bound")


def runner_workflow_runs(token: str, not_before: str) -> list[dict]:
    return complete_list(token, f"repos/{RUNNER_REPOSITORY}/actions/workflows/"
                         f"{EXTERNAL_WORKFLOW_PATH.rsplit('/', 1)[-1]}/runs?"
                         f"created=>{not_before}", "workflow_runs")


def dispatch_external(token: str, project: str, source_sha: str) -> None:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    payload = json.dumps(
        {"ref": "main", "inputs": {"project": project, "source_sha": source_sha}}
    )
    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/workflows/"
            f"{EXTERNAL_WORKFLOW_PATH.rsplit('/', 1)[-1]}/dispatches",
            "--input",
            "-",
        ],
        input=payload,
        text=True,
        check=True,
        capture_output=True,
        timeout=20,
        env=env,
    )


def emit(prefix: str, mode: str, canonical_run_id: int, external_run_id: int | None) -> None:
    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        return
    key = prefix.upper().replace("-", "_")
    with Path(github_env).open("a", encoding="utf-8") as handle:
        handle.write(f"{key}_VALIDATION_MODE={mode}\n")
        handle.write(f"{key}_CANONICAL_VALIDATION_RUN_ID={canonical_run_id}\n")
        handle.write(f"{key}_CANONICAL_RESULT="
                     f"{'SUCCESS' if mode == 'CANONICAL' else 'INFRASTRUCTURE_ALLOCATION_FAILURE'}"
                     "\n")
        handle.write(
            f"{key}_EXTERNAL_VALIDATION_RUN_ID="
            f"{external_run_id if external_run_id is not None else ''}\n"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--event", choices=("pull_request", "push"), required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--seconds", type=int, default=3600)
    args = parser.parse_args(argv)

    product_token = os.environ.get("PRODUCT_TOKEN", "")
    runner_token = os.environ.get("RUNNER_TOKEN", "")
    repository = os.environ["PRODUCT_REPOSITORY"]
    workflow_file = os.environ["VALIDATION_WORKFLOW_FILE"]
    if not product_token or not runner_token:
        raise ValidationGateError("validation gate tokens are not configured")

    try:
        conclusion = wait_exact_validation(
            product_run_reader(product_token, repository),
            repository=repository,
            workflow_path=f".github/workflows/{workflow_file}",
            run_id=args.run_id,
            head_sha=args.head_sha,
            event=args.event,
            seconds=args.seconds,
        )
    except SourceValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if conclusion == "success":
        emit(args.output_prefix, "CANONICAL", args.run_id, None)
        print(f"success {args.head_sha}")
        return 0

    canonical_run = gh_json(
        product_token,
        f"repos/{repository}/actions/runs/{args.run_id}",
    )
    # Re-read identity after waiting; a changed/mismatched run is never fallback authority.
    if not exact_canonical(canonical_run, repository=repository,
                           workflow_path=f".github/workflows/{workflow_file}",
                           source_sha=args.head_sha, event=args.event, run_id=args.run_id):
        raise ValidationGateError("canonical validation identity changed")
    jobs = complete_list(product_token,
                         f"repos/{repository}/actions/runs/{args.run_id}/jobs?filter=latest",
                         "jobs")
    if not is_runner_allocation_failure(canonical_run, jobs):
        print(
            f"CANONICAL_VALIDATION_FAILED run={args.run_id} head={args.head_sha} "
            "without hosted-runner allocation-failure signature",
            file=sys.stderr,
        )
        return 1

    if os.environ["GITHUB_REPOSITORY"] != RUNNER_REPOSITORY:
        raise ValidationGateError("fallback must execute in the trusted Runner repository")
    workflow = gh_json(runner_token, f"repos/{RUNNER_REPOSITORY}/actions/workflows/"
                       f"{EXTERNAL_WORKFLOW_PATH.rsplit('/', 1)[-1]}")
    if workflow.get("path") != EXTERNAL_WORKFLOW_PATH or workflow.get("state") != "active":
        raise ValidationGateError("trusted external workflow identity is invalid")
    workflow_id = workflow.get("id")
    not_before = str(canonical_run.get("updated_at") or "")
    existing = matching_external_success(
        runner_workflow_runs(runner_token, not_before),
        project=args.project,
        source_sha=args.head_sha,
        not_before=not_before,
        workflow_id=workflow_id,
    )
    if existing is not None:
        external_id = int(existing["id"])
        emit(args.output_prefix, "EXTERNAL_INFRA_FALLBACK", args.run_id, external_id)
        print(f"success {args.head_sha}")
        return 0

    before = runner_workflow_runs(runner_token, not_before)
    minimum_run_id = max(
        (int(run.get("id") or 0) for run in before if isinstance(run, dict)),
        default=0,
    )
    dispatch_external(runner_token, args.project, args.head_sha)

    deadline = time.monotonic() + min(args.seconds, 3600)
    selected_id = None
    while time.monotonic() < deadline:
        selected = matching_dispatched_external_run(
            runner_workflow_runs(runner_token, not_before),
            project=args.project,
            source_sha=args.head_sha,
            minimum_run_id=minimum_run_id,
            not_before=not_before,
            workflow_id=workflow_id,
        )
        if selected is None:
            time.sleep(10)
            continue
        if selected_id is not None and selected["id"] != selected_id:
            raise ValidationGateError("external validation selected run changed")
        selected_id = selected["id"]
        status = selected.get("status")
        if status != "completed":
            time.sleep(10)
            continue
        if selected.get("conclusion") != "success":
            print(
                f"EXTERNAL_VALIDATION_FAILED run={selected.get('id')} "
                f"head={args.head_sha} conclusion={selected.get('conclusion')}",
                file=sys.stderr,
            )
            return 1
        external_id = int(selected["id"])
        emit(args.output_prefix, "EXTERNAL_INFRA_FALLBACK", args.run_id, external_id)
        print(f"success {args.head_sha}")
        return 0

    print(f"EXTERNAL_VALIDATION_TIMEOUT head={args.head_sha}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
