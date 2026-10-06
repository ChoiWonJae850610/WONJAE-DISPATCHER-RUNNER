"""Trusted parent: remote identity guards and fresh child/session for initial next."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from wonjae_dispatcher_runner.direct_worker import (
    DirectWorkerError,
    git_head,
    load_direct_worker_route,
)
from wonjae_dispatcher_runner.initial_turn_recovery import run_initial_turns


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--product-checkout", required=True)
    parser.add_argument("--command", choices=("next", "retry", "resume"), required=True)
    parser.add_argument("--failure-log")
    parser.add_argument("--result-file", required=True)
    args = parser.parse_args()
    registry = Path(args.registry)
    route = load_direct_worker_route(registry, args.project)
    repo = Path(args.product_checkout).resolve()
    original_sha = git_head(repo)
    original_registry = registry.read_bytes()
    state_path = repo / route.state_path if route.state_path else None
    original_state = state_path.read_bytes() if state_path else None
    token = os.environ.get("PRODUCT_TOKEN", "")
    runner_token = os.environ.get("RUNNER_TOKEN", "")
    if not token or not runner_token:
        raise DirectWorkerError("initial recovery trusted credentials are unavailable")

    def read(endpoint: str, credential: str):
        env = os.environ.copy()
        env["GH_TOKEN"] = credential
        result = subprocess.run(
            ["gh", "api", "--method", "GET", endpoint], check=True,
            capture_output=True, text=True, env=env, timeout=20,
        )
        return json.loads(result.stdout)

    def verify_authority():
        if registry.read_bytes() != original_registry:
            raise DirectWorkerError("initial recovery routing/scope changed")
        if state_path and state_path.read_bytes() != original_state:
            raise DirectWorkerError("initial recovery execution-state/task/scope changed")
        branch = read(f"repos/{route.repository}/branches/{route.branch}", token)
        if branch.get("commit", {}).get("sha") != original_sha:
            raise DirectWorkerError("initial recovery remote active HEAD changed")
        pulls = read(f"repos/{route.repository}/pulls?state=open&per_page=100", token)
        if not isinstance(pulls, list) or len(pulls) == 100:
            raise DirectWorkerError("initial recovery cannot establish writer inventory")
        if any(p.get("base", {}).get("ref") == route.branch and
               p.get("head", {}).get("ref", "").startswith(("direct/", "job/"))
               for p in pulls):
            raise DirectWorkerError("initial recovery source writer conflict")
        runners = read(
            f"repos/{route.runner_repository}/actions/workflows/direct-worker.yml/runs"
            "?status=in_progress&per_page=100", runner_token,
        ).get("workflow_runs")
        if not isinstance(runners, list) or len(runners) == 100:
            raise DirectWorkerError("initial recovery cannot establish runner inventory")
        if any(str(run.get("id")) != os.environ.get("GITHUB_RUN_ID") and
               run.get("display_title", "").startswith(f"Direct Worker {route.project} ")
               for run in runners):
            raise DirectWorkerError("initial recovery active runner conflict")

    def run_turn(attempt: int) -> int:
        # Each child calls Codex()/thread_start anew. Tokens never reach that SDK process.
        result_file = Path(args.result_file)
        if result_file.exists():
            raise DirectWorkerError("initial recovery result file already exists")
        child_env = {key: value for key, value in os.environ.items()
                     if key not in {"GH_TOKEN", "PRODUCT_TOKEN", "RUNNER_TOKEN"}}
        return subprocess.run(
            [sys.executable, str(Path(__file__).with_name("run_direct_worker.py")),
             *sys.argv[1:]], env=child_env, check=False,
        ).returncode

    def record(attempt: int, outcome: str):
        print(f"INITIAL_TURN attempt={attempt} outcome={outcome} source={original_sha}",
              flush=True)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as handle:
                handle.write(f"- initial_turn_attempt: `{attempt}`; outcome: `{outcome}`; "
                             f"source_sha: `{original_sha}`; rejected_commit_budget: `0`\n")

    if args.command != "next":
        return run_turn(1)
    return run_initial_turns(repo, run_turn=run_turn, verify_authority=verify_authority,
                             record=record)


if __name__ == "__main__":
    raise SystemExit(main())
