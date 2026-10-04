from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

from wonjae_dispatcher_runner.source_queue import (
    QUEUE_HEADING,
    QUEUE_TERMINAL_HEADING,
    WakeIdentity,
    can_advance_after_source_run,
    latest_queue_evidence,
    parse_wake_title,
    predecessor_issue_number,
    previous_open_wake,
    project_wakes,
    queue_state,
    terminal_result,
)


def run(command: Sequence[str]) -> str:
    result = subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return result.stdout


def gh_json(command: Sequence[str]) -> Any:
    output = run(command)
    return json.loads(output or "null")


def issue_view(repository: str, issue_number: int) -> dict[str, Any]:
    value = gh_json(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repository,
            "--json",
            "number,title,state,author,comments",
        ]
    )
    if not isinstance(value, dict):
        raise RuntimeError("GitHub issue readback was not an object")
    return value


def issue_comments(issue: Mapping[str, Any]) -> list[str]:
    comments = issue.get("comments")
    if not isinstance(comments, list):
        return []
    return [str(comment.get("body") or "") for comment in comments if isinstance(comment, dict)]


def list_issues(repository: str, state: str) -> list[dict[str, Any]]:
    value = gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            state,
            "--limit",
            "1000",
            "--json",
            "number,title,state,author",
        ]
    )
    if not isinstance(value, list):
        raise RuntimeError("GitHub issue list was not an array")
    return [item for item in value if isinstance(item, dict)]


def exact_issue_by_title(repository: str, title: str) -> dict[str, Any]:
    matches = [item for item in list_issues(repository, "all") if item.get("title") == title]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one wake issue for title, found {len(matches)}")
    return issue_view(repository, int(matches[0]["number"]))


def owner_login(issue: Mapping[str, Any]) -> str:
    author = issue.get("author")
    if isinstance(author, Mapping):
        return str(author.get("login") or "")
    return ""


def require_owner_wake(
    issue: Mapping[str, Any],
    *,
    owner: str,
    expected_project: str | None = None,
    expected_title: str | None = None,
    require_open: bool = False,
) -> WakeIdentity:
    title = str(issue.get("title") or "")
    identity = parse_wake_title(title)
    if identity is None:
        raise RuntimeError("issue is not an exact Dispatcher v2 source wake")
    if expected_project and identity.project != expected_project:
        raise RuntimeError("source wake project mismatch")
    if expected_title and title != expected_title:
        raise RuntimeError("source wake title mismatch")
    if owner_login(issue) != owner:
        raise RuntimeError("source wake is not owner-authored")
    if require_open and str(issue.get("state") or "").upper() != "OPEN":
        raise RuntimeError("source wake is not open")
    return identity


def post_comment(repository: str, issue_number: int, body: str) -> None:
    run(
        [
            "gh",
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            repository,
            "--body",
            body,
        ]
    )


def queue_comment(
    identity: WakeIdentity,
    *,
    issue_number: int,
    state: str,
    controller_run_id: str,
    predecessor_issue: int | None = None,
    predecessor_task: str = "",
    note: str = "",
) -> str:
    lines = [
        QUEUE_HEADING,
        "",
        f"- project: `{identity.project}`",
        f"- task_id: `{identity.task_id}`",
        f"- control_sha: `{identity.control_sha}`",
        f"- source_sha: `{identity.source_sha}`",
        f"- wake_issue: `{issue_number}`",
        f"- queue_state: `{state}`",
        f"- controller_run_id: `{controller_run_id}`",
        f"- workflow_file: `{identity.workflow_file}`",
    ]
    if predecessor_issue is not None:
        lines.append(f"- predecessor_issue: `{predecessor_issue}`")
    if predecessor_task:
        lines.append(f"- predecessor_task: `{predecessor_task}`")
    if note:
        lines.append(f"- note: `{note}`")
    return "\n".join(lines)


def dispatch_issue(
    repository: str,
    issue: Mapping[str, Any],
    identity: WakeIdentity,
    *,
    controller_run_id: str,
    predecessor_issue: int | None,
    predecessor_task: str,
) -> None:
    issue_number = int(issue["number"])
    dispatched = queue_comment(
        identity,
        issue_number=issue_number,
        state="DISPATCHED",
        controller_run_id=controller_run_id,
        predecessor_issue=predecessor_issue,
        predecessor_task=predecessor_task,
        note="runner-owned persistent queue dispatch",
    )
    post_comment(repository, issue_number, dispatched)
    try:
        run(
            [
                "gh",
                "workflow",
                "run",
                identity.workflow_file,
                "--repo",
                repository,
                "-f",
                f"wake_issue_number={issue_number}",
                "-f",
                f"wake_title={issue['title']}",
            ]
        )
    except Exception:
        released = queue_comment(
            identity,
            issue_number=issue_number,
            state="QUEUED",
            controller_run_id=controller_run_id,
            predecessor_issue=predecessor_issue,
            predecessor_task=predecessor_task,
            note="workflow dispatch failed; claim released for retry",
        )
        post_comment(repository, issue_number, released)
        raise


def intake(args: argparse.Namespace) -> int:
    issue = issue_view(args.repository, args.issue_number)
    identity = require_owner_wake(issue, owner=args.owner, require_open=True)
    comments = issue_comments(issue)
    if terminal_result(comments):
        print(json.dumps({"action": "TERMINAL", "issue": args.issue_number}))
        return 0
    if queue_state(comments) == "DISPATCHED":
        print(json.dumps({"action": "ALREADY_DISPATCHED", "issue": args.issue_number}))
        return 0

    open_wakes = project_wakes(
        list_issues(args.repository, "open"),
        owner=args.owner,
        project=identity.project,
    )
    current_number = int(issue["number"])
    existing_predecessor = predecessor_issue_number(comments)

    if existing_predecessor is not None:
        predecessor = issue_view(args.repository, existing_predecessor)
        predecessor_identity = require_owner_wake(
            predecessor,
            owner=args.owner,
            expected_project=identity.project,
        )
        predecessor_result = terminal_result(issue_comments(predecessor))
        if predecessor_result != "COMPLETED":
            body = queue_comment(
                identity,
                issue_number=current_number,
                state="QUEUED",
                controller_run_id=args.controller_run_id,
                predecessor_issue=existing_predecessor,
                predecessor_task=predecessor_identity.task_id,
                note=f"waiting for predecessor terminal COMPLETED; current={predecessor_result or 'NONTERMINAL'}",
            )
            post_comment(args.repository, current_number, body)
            print(
                json.dumps(
                    {
                        "action": "QUEUED",
                        "issue": current_number,
                        "predecessor": existing_predecessor,
                        "predecessor_result": predecessor_result or "NONTERMINAL",
                    }
                )
            )
            return 0

        dispatch_issue(
            args.repository,
            issue,
            identity,
            controller_run_id=args.controller_run_id,
            predecessor_issue=existing_predecessor,
            predecessor_task=predecessor_identity.task_id,
        )
        print(json.dumps({"action": "DISPATCHED", "issue": current_number}))
        return 0

    predecessor = previous_open_wake(current_number, open_wakes)
    if predecessor is not None:
        predecessor_identity = require_owner_wake(
            predecessor,
            owner=args.owner,
            expected_project=identity.project,
        )
        predecessor_number = int(predecessor["number"])
        body = queue_comment(
            identity,
            issue_number=current_number,
            state="QUEUED",
            controller_run_id=args.controller_run_id,
            predecessor_issue=predecessor_number,
            predecessor_task=predecessor_identity.task_id,
            note="waiting for immediately preceding source task",
        )
        post_comment(args.repository, current_number, body)
        print(
            json.dumps(
                {
                    "action": "QUEUED",
                    "issue": current_number,
                    "predecessor": predecessor_number,
                }
            )
        )
        return 0

    dispatch_issue(
        args.repository,
        issue,
        identity,
        controller_run_id=args.controller_run_id,
        predecessor_issue=None,
        predecessor_task="",
    )
    print(json.dumps({"action": "DISPATCHED", "issue": current_number}))
    return 0


def verify_claim(args: argparse.Namespace) -> int:
    issue = issue_view(args.repository, args.issue_number)
    identity = require_owner_wake(
        issue,
        owner=args.owner,
        expected_project=args.project,
        expected_title=args.wake_title,
        require_open=True,
    )
    evidence = latest_queue_evidence(issue_comments(issue))
    if evidence is None or evidence.state != "DISPATCHED":
        raise RuntimeError("wake issue does not have a live queue dispatch claim")
    if evidence.fields.get("workflow_file") != identity.workflow_file:
        raise RuntimeError("queue dispatch claim workflow mismatch")
    print(
        json.dumps(
            {
                "action": "CLAIM_VERIFIED",
                "project": identity.project,
                "task_id": identity.task_id,
                "issue": args.issue_number,
            }
        )
    )
    return 0


def record_completed(args: argparse.Namespace) -> int:
    issue = issue_view(args.repository, args.issue_number)
    identity = require_owner_wake(
        issue,
        owner=args.owner,
        expected_project=args.project,
        expected_title=args.wake_title,
        require_open=True,
    )
    if queue_state(issue_comments(issue)) != "DISPATCHED":
        raise RuntimeError("completed source task has no live queue dispatch claim")
    body = "\n".join(
        [
            QUEUE_TERMINAL_HEADING,
            "",
            f"- project: `{identity.project}`",
            f"- task_id: `{identity.task_id}`",
            f"- attempt: `{args.attempt}`",
            f"- control_sha: `{identity.control_sha}`",
            f"- source_sha: `{identity.source_sha}`",
            f"- wake_issue: `{args.issue_number}`",
            "- result: `COMPLETED`",
            f"- source_run_id: `{args.source_run_id}`",
            f"- product_pr: `{args.product_pr}`",
            f"- integrated_sha: `{args.integrated_sha}`",
            f"- exact_integrated_sha_validation_run: `{args.validation_run}`",
            "- active_writer: `RELEASED_BY_MERGED_PR`",
        ]
    )
    post_comment(args.repository, args.issue_number, body)
    print(json.dumps({"action": "COMPLETED_RECORDED", "issue": args.issue_number}))
    return 0


def advance(args: argparse.Namespace) -> int:
    if args.conclusion != "success":
        print(json.dumps({"action": "STOPPED_NON_SUCCESS", "conclusion": args.conclusion}))
        return 0

    completed = exact_issue_by_title(args.repository, args.wake_title)
    identity = require_owner_wake(
        completed,
        owner=args.owner,
        expected_title=args.wake_title,
    )
    completed_number = int(completed["number"])
    completed_comments = issue_comments(completed)
    if str(completed.get("state") or "").upper() != "CLOSED":
        print(json.dumps({"action": "WAIT_COMPLETION_CLOSE", "issue": completed_number}))
        return 0
    if not can_advance_after_source_run(
        conclusion=args.conclusion,
        completed_comments=completed_comments,
        source_run_id=args.source_run_id,
    ):
        print(json.dumps({"action": "STOPPED_NO_EXACT_COMPLETION", "issue": completed_number}))
        return 0

    open_wakes = project_wakes(
        list_issues(args.repository, "open"),
        owner=args.owner,
        project=identity.project,
    )
    if not open_wakes:
        print(json.dumps({"action": "QUEUE_EMPTY", "project": identity.project}))
        return 0

    candidate = issue_view(args.repository, int(open_wakes[0]["number"]))
    candidate_identity = require_owner_wake(
        candidate,
        owner=args.owner,
        expected_project=identity.project,
        require_open=True,
    )
    candidate_comments = issue_comments(candidate)
    if queue_state(candidate_comments) == "DISPATCHED":
        print(json.dumps({"action": "NEXT_ALREADY_DISPATCHED", "issue": candidate["number"]}))
        return 0

    predecessor_number = predecessor_issue_number(candidate_comments)
    if predecessor_number != completed_number:
        print(
            json.dumps(
                {
                    "action": "STOPPED_LINEAGE_MISMATCH",
                    "candidate": candidate["number"],
                    "expected_predecessor": completed_number,
                    "actual_predecessor": predecessor_number,
                }
            )
        )
        return 0
    if terminal_result(completed_comments) != "COMPLETED":
        print(json.dumps({"action": "STOPPED_PREDECESSOR_NOT_COMPLETED"}))
        return 0

    dispatch_issue(
        args.repository,
        candidate,
        candidate_identity,
        controller_run_id=args.controller_run_id,
        predecessor_issue=completed_number,
        predecessor_task=identity.task_id,
    )
    print(
        json.dumps(
            {
                "action": "DISPATCHED_NEXT",
                "completed_issue": completed_number,
                "next_issue": candidate["number"],
            }
        )
    )
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)

    intake_parser = sub.add_parser("intake")
    intake_parser.add_argument("--repository", required=True)
    intake_parser.add_argument("--owner", required=True)
    intake_parser.add_argument("--issue-number", type=int, required=True)
    intake_parser.add_argument("--controller-run-id", required=True)
    intake_parser.set_defaults(func=intake)

    verify_parser = sub.add_parser("verify-claim")
    verify_parser.add_argument("--repository", required=True)
    verify_parser.add_argument("--owner", required=True)
    verify_parser.add_argument("--issue-number", type=int, required=True)
    verify_parser.add_argument("--project", required=True)
    verify_parser.add_argument("--wake-title", required=True)
    verify_parser.set_defaults(func=verify_claim)

    completed_parser = sub.add_parser("record-completed")
    completed_parser.add_argument("--repository", required=True)
    completed_parser.add_argument("--owner", required=True)
    completed_parser.add_argument("--issue-number", type=int, required=True)
    completed_parser.add_argument("--project", required=True)
    completed_parser.add_argument("--wake-title", required=True)
    completed_parser.add_argument("--attempt", required=True)
    completed_parser.add_argument("--source-run-id", required=True)
    completed_parser.add_argument("--product-pr", required=True)
    completed_parser.add_argument("--integrated-sha", required=True)
    completed_parser.add_argument("--validation-run", required=True)
    completed_parser.set_defaults(func=record_completed)

    advance_parser = sub.add_parser("advance")
    advance_parser.add_argument("--repository", required=True)
    advance_parser.add_argument("--owner", required=True)
    advance_parser.add_argument("--wake-title", required=True)
    advance_parser.add_argument("--source-run-id", required=True)
    advance_parser.add_argument("--conclusion", required=True)
    advance_parser.add_argument("--controller-run-id", required=True)
    advance_parser.set_defaults(func=advance)

    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
