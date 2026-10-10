"""Trusted docs-only recovery PR publisher for an exact completed failed Owner Direct Worker.

Runs ONLY from a separately authorized trusted-main recovery workflow. The
failed Codex job is NEVER retried here. Product writes are a 2-file PR only.
Do not log raw GitHub job output or credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

from wonjae_dispatcher_runner.failure_recovery_document import (
    RecoveryPreparationError,
    TimeoutEvidence,
    parse_initial_turn_timeout,
    recovery_paths,
    render_recovery,
)

RUNNER = "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER"
CONTROL = "ChoiWonJae850610/DEV-CONTROL"
SHA = re.compile(r"^[0-9a-f]{40}$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9-]{1,30}$")


def github_api(endpoint: str, token: str, *, method: str = "GET",
               fields: dict[str, str] | None = None) -> object:
    if not token or len(endpoint) > 550 or not endpoint.startswith("repos/"):
        raise RecoveryPreparationError("invalid credential or GitHub endpoint")
    args = ["gh", "api", "--method", method, endpoint]
    for key, value in (fields or {}).items():
        if key not in {"title", "body", "head", "base"}:
            raise RecoveryPreparationError("unexpected GitHub mutation parameter")
        args += ["-f", f"{key}={value}"]
    env = dict(os.environ, GH_TOKEN=token)
    response = subprocess.run(args, env=env, check=True, capture_output=True,
                              text=True, timeout=40)
    if len(response.stdout) > 500_000:
        raise RecoveryPreparationError("unbounded GitHub metadata")
    return json.loads(response.stdout)


def checked_run(run: object, expected_project: str, runner_sha: str) -> None:
    if not isinstance(run, dict) or not PROJECT.fullmatch(expected_project):
        raise RecoveryPreparationError("invalid failed run identity")
    if (run.get("repository", {}).get("full_name") != RUNNER
            or run.get("name") != f"Direct Worker {expected_project} next"
            or run.get("display_title") != f"Direct Worker {expected_project} next"
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != "main"
            or run.get("path", "").split("@")[0] != ".github/workflows/direct-worker.yml"
            or run.get("head_sha") != runner_sha
            or run.get("status") != "completed"
            or run.get("conclusion") != "failure"
            or run.get("run_attempt") != 1
            or run.get("actor", {}).get("login") != "ChoiWonJae850610"
            or not isinstance(run.get("id"), int) or run["id"] < 1):
        raise RecoveryPreparationError("failed run is not an exact eligible source next")



def checked_trusted_runner_history(
    failed_head_sha: str, current_main_sha: str, read_token: str
) -> None:
    """Admit a historical exact Owner run only on trusted main ancestry.

    A recovery job retried after its initial run can check out newer trusted
    main code. Requiring the failed run SHA to equal current main would wrongly
    reject every historical recovery after a common Runner fix. This does not
    grant permission to rerun source; it only verifies old main ancestry.
    """
    if not SHA.fullmatch(failed_head_sha) or not SHA.fullmatch(current_main_sha):
        raise RecoveryPreparationError("invalid runner history identity")
    comparison = github_api(
        f"repos/{RUNNER}/compare/{failed_head_sha}...{current_main_sha}",
        read_token,
    )
    if (not isinstance(comparison, dict)
            or comparison.get("status") not in {"ahead", "identical"}
            or comparison.get("base_commit", {}).get("sha") != failed_head_sha
            or comparison.get("merge_base_commit", {}).get("sha") != failed_head_sha
            or type(comparison.get("ahead_by")) is not int
            or not 0 <= comparison["ahead_by"] <= 40
            or comparison.get("behind_by") != 0
            or (comparison["ahead_by"] == 0) != (failed_head_sha == current_main_sha)):
        raise RecoveryPreparationError("failed runner is not bounded trusted main ancestry")



def checked_failure_job(value: object, project: str) -> int:
    if not isinstance(value, dict):
        raise RecoveryPreparationError("missing exact failed job inventory")
    jobs = value.get("jobs")
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 20:
        raise RecoveryPreparationError("unbounded job list")
    match = [job for job in jobs if
             job.get("name") == f"{project.lower()} / direct-worker"
             and job.get("conclusion") == "failure"
             and job.get("status") == "completed"]
    if len(match) != 1:
        raise RecoveryPreparationError("failed source job ambiguous")
    job = match[0]
    failed_steps = [step.get("name") for step in job.get("steps", [])
                    if step.get("conclusion") == "failure"]
    if (failed_steps != ["Run isolated Codex Direct Worker"]
            or not isinstance(job.get("runner_id"), int)
            or job["runner_id"] < 1
            or not isinstance(job.get("id"), int)
            or job["id"] < 1):
        raise RecoveryPreparationError("not an exact isolated source turn failure")
    return job["id"]


def read_job_log(run_id: int, job_id: int, token: str) -> str:
    env = dict(os.environ, GH_TOKEN=token)
    response = subprocess.run(
        ["gh", "run", "view", str(run_id), "--repo", RUNNER, "--job", str(job_id), "--log"],
        env=env, check=True, capture_output=True, text=True, timeout=40,
    )
    if len(response.stdout) > 2_000_000:
        raise RecoveryPreparationError("unbounded original job log")
    # Keep untrusted logs in memory only; never print or upload them.
    return response.stdout


def checked_registry(value: object, project: str) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RecoveryPreparationError("untrusted registry")
    route = value.get("projects", {}).get(project)
    if not isinstance(route, dict) or route.get("status") != "active":
        raise RecoveryPreparationError("project is not registered and active")
    if route.get("repository") != f"ChoiWonJae850610/{project}":
        raise RecoveryPreparationError("unexpected repository")
    execution = route.get("execution")
    if not isinstance(execution, dict) or execution.get("mode") != "direct_worker":
        raise RecoveryPreparationError("not a registered Direct Worker project")
    if execution.get("state_path") != ".wonjae/execution-state.yaml":
        raise RecoveryPreparationError("no expected product state path")
    branch = route.get("branch")
    if (not isinstance(branch, str) or branch in {"main", "master"}
            or not re.fullmatch(r"[a-z0-9][a-z0-9/_-]{1,90}", branch)
            or ".." in branch or "//" in branch):
        raise RecoveryPreparationError("unexpected product branch")
    return route



RECENT_SOURCE_RUN_PAGE_SIZE = 15  # Each full GitHub run is ~15 KB; stay under 500 KB GET bound.
RECENT_SOURCE_RUN_MAX_PAGES = 7  # Fail closed after at most 105 newer/exact runs.
SAFE_RECOVERY_CODES = {
    "unbounded GitHub metadata": "GITHUB_METADATA_BOUND",
    "invalid runner history identity": "RUNNER_HISTORY_IDENTITY",
    "failed runner is not bounded trusted main ancestry": "RUNNER_MAIN_ANCESTRY",
    "untrusted bounded source run inventory": "SOURCE_RUN_INVENTORY",
    "out-of-order source run inventory": "SOURCE_RUN_ORDER",
    "failed source run absent from bounded history": "FAILED_RUN_NOT_FOUND",
    "later product source run supersedes this failure": "LATER_SOURCE_RUN",
    "failed run is not an exact eligible source next": "FAILED_RUN_IDENTITY",
    "failed source job ambiguous": "FAILED_JOB_IDENTITY",
    "not an exact isolated source turn failure": "FAILED_STEP_IDENTITY",
    "initial timeout evidence is not exactly proven": "TIMEOUT_MARKERS",
    "control registry changed since checkout": "CONTROL_SHA_CHANGED",
    "current product generation is not failed source generation": "PRODUCT_SHA_CHANGED",
    "active source writer conflicts with docs recovery": "SOURCE_WRITER_ACTIVE",
    "recovery documents exceed strict limits": "RECOVERY_DOC_BOUND",
}


def checked_latest_owner_source_run(project: str, run_id: int, runner_token: str) -> None:
    """Require the exact failed run in newest-first bounded history, without bulk GETs.

    GitHub workflow-run records include large embedded metadata. A per_page=100
    request can exceed github_api's 500 KB safety limit before this freshness
    check even runs. Inspect 15 records/page (7 pages max) and fail closed if
    the original run is unavailable, ordering is ambiguous, or a later source
    run for the same product exists. This is read-only and never dispatches.
    """
    previous_id = None
    for page in range(1, RECENT_SOURCE_RUN_MAX_PAGES + 1):
        payload = github_api(
            f"repos/{RUNNER}/actions/workflows/direct-worker.yml/runs"
            f"?event=workflow_dispatch&per_page={RECENT_SOURCE_RUN_PAGE_SIZE}&page={page}",
            runner_token,
        )
        rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if (not isinstance(rows, list) or not rows
                or len(rows) > RECENT_SOURCE_RUN_PAGE_SIZE):
            raise RecoveryPreparationError("untrusted bounded source run inventory")
        for row in rows:
            item_id = row.get("id") if isinstance(row, dict) else None
            if (type(item_id) is not int or item_id < 1
                    or (previous_id is not None and item_id >= previous_id)):
                raise RecoveryPreparationError("out-of-order source run inventory")
            previous_id = item_id
            if item_id > run_id and str(row.get("display_title", "")).startswith(
                    f"Direct Worker {project} "):
                raise RecoveryPreparationError("later product source run supersedes this failure")
            if item_id == run_id:
                return
            if item_id < run_id:
                raise RecoveryPreparationError("failed source run absent from bounded history")
        if len(rows) < RECENT_SOURCE_RUN_PAGE_SIZE:
            raise RecoveryPreparationError("failed source run absent from bounded history")
    raise RecoveryPreparationError("failed source run absent from bounded history")



def clean_checkout(path: Path, sha: str) -> None:
    got = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout
    if got != sha or dirty:
        raise RecoveryPreparationError("product checkout not clean at exact active SHA")


def checked_recovery_write_paths(
    checkout: Path, state_path: Path, target: Path | None = None
) -> None:
    """Reject symlink aliases and non-regular paths before any product file mutation."""
    if checkout.is_symlink() or not checkout.is_dir():
        raise RecoveryPreparationError("untrusted product checkout path")
    if state_path != checkout / ".wonjae/execution-state.yaml":
        raise RecoveryPreparationError("unexpected execution-state write path")
    state_dir = state_path.parent
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise RecoveryPreparationError("execution-state directory alias")
    if state_path.is_symlink() or not state_path.is_file():
        raise RecoveryPreparationError("execution-state file alias")
    if target is not None:
        docs_dir = checkout / "docs"
        operations_dir = docs_dir / "operations"
        if target.parent != operations_dir:
            raise RecoveryPreparationError("unexpected documentation write path")
        for directory in (docs_dir, operations_dir):
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise RecoveryPreparationError("documentation directory alias")
        if target.is_symlink() or target.exists():
            raise RecoveryPreparationError("recovery documentation path already exists")


def run_command(args: list[str], *, cwd: Path, token: str) -> str:
    env = dict(os.environ, GH_TOKEN=token)
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                            text=True, env=env, timeout=75)
    return result.stdout.rstrip("\n")


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def plan(event: dict, project: str, runner_token: str, product_token: str,
         control_token: str, workdir: Path, registry_path: Path) -> None:
    if event.get("action") != "completed":
        raise RecoveryPreparationError("not a terminal workflow_run event")
    candidate = event.get("workflow_run")
    if not isinstance(candidate, dict):
        raise RecoveryPreparationError("missing failed run")
    run_id = candidate.get("id")
    if not isinstance(run_id, int) or run_id < 1:
        raise RecoveryPreparationError("invalid original run ID")
    runner_sha = github_api(f"repos/{RUNNER}/branches/main", runner_token)["commit"]["sha"]
    if not SHA.fullmatch(runner_sha):
        raise RecoveryPreparationError("invalid trusted Runner SHA")
    actual = github_api(f"repos/{RUNNER}/actions/runs/{run_id}", runner_token)
    if (candidate.get("id") != actual.get("id")
            or candidate.get("head_sha") != actual.get("head_sha")):
        raise RecoveryPreparationError("event and exact run disagree")
    recorded_sha = actual.get("head_sha")
    if not isinstance(recorded_sha, str) or not SHA.fullmatch(recorded_sha):
        raise RecoveryPreparationError("invalid runner history identity")
    checked_run(actual, project, recorded_sha)
    checked_trusted_runner_history(recorded_sha, runner_sha, runner_token)
    failed_job = checked_failure_job(
        github_api(f"repos/{RUNNER}/actions/runs/{run_id}/jobs?per_page=100",
                   runner_token), project)
    logs = read_job_log(run_id, failed_job, runner_token)
    # Extract the original source SHA *only* from the three trusted known markers.
    sources = re.findall(
        r"INITIAL_TURN attempt=1 outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS "
        r"source=([0-9a-f]{40})", logs,
    )
    if len(sources) != 1 or not parse_initial_turn_timeout(logs, sources[0]):
        raise RecoveryPreparationError("initial timeout evidence is not exactly proven")
    original_sha = sources[0]
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    route = checked_registry(registry, project)
    control_live = github_api(f"repos/{CONTROL}/branches/main", control_token)
    local_control = run_command(["git", "rev-parse", "HEAD"],
                                cwd=registry_path.parent, token=control_token)
    if control_live["commit"]["sha"] != local_control:
        raise RecoveryPreparationError("control registry changed since checkout")
    repo = route["repository"]
    branch = route["branch"]
    remote = github_api(f"repos/{repo}/branches/{branch}", product_token)
    source_date = remote["commit"]["commit"]["committer"]["date"]
    if (remote["commit"]["sha"] != original_sha
            or timestamp(actual["created_at"]) <= timestamp(source_date)):
        raise RecoveryPreparationError("current product generation is not failed source generation")
    open_prs = github_api(f"repos/{repo}/pulls?state=open&per_page=100", product_token)
    if not isinstance(open_prs, list) or len(open_prs) >= 100:
        raise RecoveryPreparationError("cannot prove unique writer inventory")
    if any(p["base"]["ref"] == branch and p["head"]["ref"].startswith(("job/", "direct/"))
           for p in open_prs):
        raise RecoveryPreparationError("active source writer conflicts with docs recovery")
    # Verify exact original Owner run without requesting an oversized 100-run payload.
    checked_latest_owner_source_run(project, run_id, runner_token)
    product_checkout = workdir / "product"
    if workdir.is_symlink() or workdir.exists():
        raise RecoveryPreparationError("recovery workspace must be new and isolated")
    workdir.mkdir(mode=0o700, parents=True)
    run_command(["gh", "auth", "setup-git"], cwd=workdir, token=product_token)
    run_command(["git", "clone", "--no-checkout", f"https://github.com/{repo}.git",
                 str(product_checkout)], cwd=workdir, token=product_token)
    run_command(["git", "-C", str(product_checkout), "fetch", "--no-tags",
                 "origin", original_sha], cwd=workdir, token=product_token)
    run_command(["git", "-C", str(product_checkout), "checkout", "--detach", "FETCH_HEAD"],
                cwd=workdir, token=product_token)
    clean_checkout(product_checkout, original_sha)
    state_path = product_checkout / ".wonjae/execution-state.yaml"
    checked_recovery_write_paths(product_checkout, state_path)
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    action = state.get("next_action") if isinstance(state, dict) else None
    if not isinstance(action, dict) or not isinstance(action.get("source_task_id"), str):
        raise RecoveryPreparationError("current source task identity missing")
    identity = TimeoutEvidence(
        project, repo, branch, original_sha, run_id, action["source_task_id"],
    )
    rec_branch, doc_path, allowed_state_path = recovery_paths(identity)
    matching = [p for p in open_prs if p["head"]["ref"] == rec_branch]
    if matching:
        print(f"RECOVERY_PREP_ALREADY_PRESENT project={project} run_id={run_id}")
        return
    doc, state_text = render_recovery(identity, state)
    run_command(["git", "-C", str(product_checkout), "switch", "-c", rec_branch],
                cwd=workdir, token=product_token)
    target = product_checkout / doc_path
    checked_recovery_write_paths(product_checkout, state_path, target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    checked_recovery_write_paths(product_checkout, state_path, target)
    target.write_text(doc, encoding="utf-8")
    state_path.write_text(state_text, encoding="utf-8")
    tracked = run_command(
        ["git", "-C", str(product_checkout), "status", "--porcelain", "--untracked-files=all"],
        cwd=workdir, token=product_token,
    )
    if set(tracked.splitlines()) != {"?? " + doc_path, " M " + allowed_state_path}:
        raise RecoveryPreparationError("recovery file mutations exceed exact two-file allowlist")
    # One last live active source check before publishing this unmerged docs PR.
    latest_head = github_api(f"repos/{repo}/branches/{branch}", product_token)
    if latest_head["commit"]["sha"] != original_sha:
        raise RecoveryPreparationError("active branch changed while preparing recovery docs")
    run_command(["git", "add", "--", doc_path, allowed_state_path],
                cwd=product_checkout, token=product_token)
    staged = run_command(["git", "diff", "--cached", "--name-only"],
                         cwd=product_checkout, token=product_token)
    if set(staged.splitlines()) != {doc_path, allowed_state_path}:
        raise RecoveryPreparationError("unexpected staged path")
    run_command(["git", "-c", "user.name=WONJAE trusted docs recovery",
                 "-c", "user.email=trusted-recovery@users.noreply.github.com",
                 "commit", "-m", f"docs: prepare exact failed run {run_id} recovery gate"],
                cwd=product_checkout, token=product_token)
    run_command(["git", "push", "origin", f"HEAD:refs/heads/{rec_branch}"],
                cwd=product_checkout, token=product_token)
    # The PR is never merged or given source/provider authority by this workflow.
    pr = github_api(f"repos/{repo}/pulls", product_token, method="POST", fields={
        "title": f"docs: recovery preparation for {identity.task_id} failed run {run_id}",
        "head": rec_branch,
        "base": branch,
        "body": (
            f"Automated docs-only preparation bound to original Owner Direct Worker #{run_id}, "
            f"source {original_sha} and task {identity.task_id}. The exact trusted "
            "initial-turn timeout signature was verified. Current state becomes "
            "DECISION_REQUIRED pending Codex/Runner readiness check, not SOURCE_READY. "
            "No source, provider, EAS, credential, Production or QA action executed. "
            "Only the SANJINWORKS authenticated document-merge Owner button may "
            "integrate this PR after exact PR-head validation."
        ),
    })
    if not isinstance(pr, dict) or pr.get("state") != "open" or pr.get("base", {}).get(
            "ref") != branch or pr.get("head", {}).get("ref") != rec_branch:
        raise RecoveryPreparationError("product documentation PR receipt not verified")
    number = pr.get("number")
    if not isinstance(number, int) or number < 1:
        raise RecoveryPreparationError("product recovery PR number unavailable")
    print(f"RECOVERY_DOCS_PR_PREPARED project={project} run_id={run_id} pr={number}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    a = parser.parse_args()
    try:
        event = json.loads(a.event.read_text(encoding="utf-8"))
        plan(event, a.project, os.environ["RUNNER_READ_TOKEN"],
             os.environ["PRODUCT_WRITE_TOKEN"], os.environ["CONTROL_READ_TOKEN"],
             a.workdir, a.registry)
    except (RecoveryPreparationError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError, OSError, yaml.YAMLError) as exc:
        # Do not stringify arbitrary subprocess/JSON exception bodies into
        # a public GitHub log. Diagnosis remains the separate FAILED state.
        code = SAFE_RECOVERY_CODES.get(
            str(exc) if isinstance(exc, RecoveryPreparationError) else "",
            "UNCLASSIFIED",
        )
        print(f"RECOVERY_PREP_HALTED reason={type(exc).__name__} code={code}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
