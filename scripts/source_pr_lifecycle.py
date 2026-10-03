from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wonjae_dispatcher_runner.source_pr_lifecycle import (  # noqa: E402
    GhClient,
    SourceIdentity,
    diagnose_open_job_prs,
    terminalize_exact_pr,
)


def _identity(args: argparse.Namespace) -> SourceIdentity:
    return SourceIdentity(
        project=args.project,
        repository=args.repository,
        target_branch=args.target_branch,
        task_id=args.task_id,
        attempt=args.attempt,
        control_sha=args.control_sha,
        source_sha=args.source_sha,
        runner_run_id=args.runner_run_id,
    )


def _write_env(path: str, values: dict[str, object]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            safe = str(value if value is not None else "").replace("\n", " ").replace("\r", " ")
            handle.write(f"{key}={safe}\n")


def terminalize(args: argparse.Namespace) -> int:
    client = GhClient(os.environ.get("PRODUCT_GH_TOKEN", ""))
    evidence = Path(args.evidence_file).read_text(encoding="utf-8")
    result = terminalize_exact_pr(
        client,
        _identity(args),
        evidence,
        args.terminal_status,
        args.pr_number,
    )
    print(json.dumps(result.as_dict(), sort_keys=True))
    _write_env(
        args.github_env,
        {
            "SOURCE_PR_CLEANUP_STATUS": result.status,
            "SOURCE_PR_CLEANUP_PR": result.pr_number or "",
            "SOURCE_PR_CLEANUP_DETAIL": result.detail,
            "SOURCE_PR_CLEANUP_NEXT_ACTION": result.next_action,
        },
    )
    return 0


def diagnose(args: argparse.Namespace) -> int:
    client = GhClient(
        os.environ.get("PRODUCT_GH_TOKEN", ""),
        os.environ.get("RUNNER_GH_TOKEN", ""),
    )
    conflicts = diagnose_open_job_prs(
        client,
        args.project,
        args.repository,
        args.target_branch,
        args.runner_repository,
    )
    if not conflicts:
        print("SOURCE_CONCURRENCY_PASS no open job/* Product PR")
        return 0

    for conflict in conflicts:
        if conflict.kind == "STALE_TERMINAL_OPEN_PR":
            print(
                "::error::Stale terminal Dispatcher Product PR remains open and blocks "
                "concurrency."
            )
            print(
                f"{conflict.pr_number} {conflict.head_ref} task={conflict.task_id} "
                f"attempt={conflict.attempt} terminal={conflict.terminal_result} "
                f"wake_issue={conflict.wake_issue_number} cleanup_required=true"
            )
        else:
            print("::error::Another active or unresolved Dispatcher Product PR is open.")
            print(
                f"{conflict.pr_number} {conflict.head_ref} task="
                f"{conflict.task_id or 'unknown'} attempt={conflict.attempt or 'unknown'}"
            )
    return 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Dispatcher v2 source Product PR lifecycle guard.")
    sub = root.add_subparsers(dest="command", required=True)

    terminal = sub.add_parser("terminalize")
    terminal.add_argument("--project", required=True)
    terminal.add_argument("--repository", required=True)
    terminal.add_argument("--target-branch", required=True)
    terminal.add_argument("--task-id", required=True)
    terminal.add_argument("--attempt", required=True, type=int)
    terminal.add_argument("--control-sha", required=True)
    terminal.add_argument("--source-sha", required=True)
    terminal.add_argument("--runner-run-id", required=True)
    terminal.add_argument("--terminal-status", required=True)
    terminal.add_argument("--evidence-file", required=True)
    terminal.add_argument("--pr-number", type=int)
    terminal.add_argument("--github-env", default="")
    terminal.set_defaults(func=terminalize)

    conflict = sub.add_parser("diagnose-conflicts")
    conflict.add_argument("--project", required=True)
    conflict.add_argument("--repository", required=True)
    conflict.add_argument("--target-branch", required=True)
    conflict.add_argument("--runner-repository", required=True)
    conflict.set_defaults(func=diagnose)

    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
