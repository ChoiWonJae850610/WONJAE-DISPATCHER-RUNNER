"""Trusted docs-only recovery PR publisher for an exact completed failed Owner Direct Worker.

Runs ONLY from a separately authorized trusted-main recovery workflow. The
failed Codex job is NEVER retried here. Product writes are a 2-file PR only.
Do not log raw GitHub job output or credentials.
"""

from __future__ import annotations

import argparse
import base64
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
            or run.get("name") != "Direct Worker"
            or run.get("display_title") != f"Direct Worker {expected_project} next"
            or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != "main"
            or run.get("path", "").split("@")[0] != ".github/workflows/direct-worker.yml"
            or run.get("head_sha") != runner_sha
            or run.get("status") != "completed"
            or run.get("conclusion") != "failure"
            or run.get("run_attempt") != 1
            or not isinstance(run.get("id"), int) or run["id"] < 1):
        raise RecoveryPreparationError("failed run is not an exact eligible source next")


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
    if not isinstance(branch, str) or branch in {"main", "master"}:
        raise RecoveryPreparationError("protected main/master not allowed")
    return route


def clean_checkout(path: Path, sha: str) -> None:
    got = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout
    if got != sha or dirty:
        raise RecoveryPreparationError("product checkout not clean at exact active SHA")


def run_command(args: list[str], *, cwd: Path, token: str) -> str:
    env = dict(os.environ, GH_TOKEN=token)
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                            text=True, env=env, timeout=75)
    return result.stdout.strip()


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
    if candidate.get("id") != actual.get("id") or candidate.get("head_sha") != actual.get("head_sha"):
        raise RecoveryPreparationError("event and exact run disagree")
    checked_run(actual, project, runner_sha)
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
    if remote["commit"]["sha"] != original_sha or timestamp(actual["created_at"]) <= timestamp(source_date):
        raise RecoveryPreparationError("current product generation is not failed source generation")
    open_prs = github_api(f"repos/{repo}/pulls?state=open&per_page=100", product_token)
    if not isinstance(open_prs, list) or len(open_prs) >= 100:
        raise RecoveryPreparationError("cannot prove unique writer inventory")
    if any(p["base"]["ref"] == branch and p["head"]["ref"].startswith(("job/", "direct/"))
           for p in open_prs):
        raise RecoveryPreparationError("active source writer conflicts with docs recovery")
    latest = github_api(
        f"repos/{RUNNER}/actions/workflows/direct-worker.yml/runs"
        "?event=workflow_dispatch&per_page=100", runner_token,
    )
    rows = latest.get("workflow_runs", [])
    if not isinstance(rows, list) or len(rows) >= 100:
        raise RecoveryPreparationError("cannot prove latest Owner run")
    if any(x.get("id", 0) > run_id and x.get("display_title", "").startswith(
            f"Direct Worker {project} ") for x in rows):
        raise RecoveryPreparationError("later product source run supersedes this failure")
    identity = TimeoutEvidence(project, repo, branch, original_sha, run_id, "")
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
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise RecoveryPreparationError("recovery documentation file already exists")
    target.write_text(doc, encoding="utf-8")
    state_path.write_text(state_text, encoding="utf-8")
    tracked = run_command(
        ["git", "-C", str(product_checkout), "status", "--porcelain", "--untracked-files=all"],
        cwd=workdir, token=product_token,
    )
    if set(tracked.splitlines()) != {"?? " + doc_path, " M " + allowed_state_path}:
        raise RecoveryPreparationError("recovery file mutations exceed exact two-file allowlist")
    # One last live active source check before publishing this unmerged docs PR.
    if github_api(f"repos/{repo}/branches/{branch}", product_token)["commit"]["sha"] != original_sha:
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
        print(f"RECOVERY_PREP_HALTED reason={type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
