from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from wonjae_dispatcher_runner.source_queue import (
    QUEUE_HEADING,
    QUEUE_TERMINAL_HEADING,
    SourceAuthority,
    SourceRegistration,
    completion_matches_source_run,
    completion_source_sha,
    latest_queue_evidence,
    parse_execution_title,
    parse_registration_title,
    project_registrations,
    queue_state,
    registration_matches_execution,
    source_authority,
    terminal_result,
)


def run(command: Sequence[str], *, token: str | None = None) -> str:
    env = os.environ.copy()
    if token is not None:
        env["GH_TOKEN"] = token
    result = subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
        env=env,
    )
    return result.stdout


def gh_json(command: Sequence[str], *, token: str | None = None) -> Any:
    output = run(command, token=token)
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
    return [
        str(comment.get("body") or "")
        for comment in comments
        if isinstance(comment, dict)
    ]


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


def owner_login(issue: Mapping[str, Any]) -> str:
    author = issue.get("author")
    if isinstance(author, Mapping):
        return str(author.get("login") or "")
    return ""


def require_owner_registration(
    issue: Mapping[str, Any],
    *,
    owner: str,
    expected_project: str | None = None,
    require_open: bool = False,
) -> SourceRegistration:
    registration = parse_registration_title(str(issue.get("title") or ""))
    if registration is None:
        raise RuntimeError("issue is not an exact Dispatcher v2 source registration")
    if expected_project and registration.project != expected_project:
        raise RuntimeError("source registration project mismatch")
    if owner_login(issue) != owner:
        raise RuntimeError("source registration is not owner-authored")
    if require_open and str(issue.get("state") or "").upper() != "OPEN":
        raise RuntimeError("source registration is not open")
    return registration


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


def close_issue(repository: str, issue_number: int) -> None:
    run(
        [
            "gh",
            "issue",
            "close",
            str(issue_number),
            "--repo",
            repository,
            "--reason",
            "completed",
        ]
    )


def fetch_control_record(
    *,
    control_repository: str,
    control_token: str,
    registration: SourceRegistration,
) -> dict[str, Any]:
    path = f"tasks-v2/{registration.project}/{registration.task_id}.json"
    value = gh_json(
        [
            "gh",
            "api",
            f"repos/{control_repository}/contents/{path}?ref={registration.control_sha}",
        ],
        token=control_token,
    )
    if not isinstance(value, dict) or not isinstance(value.get("content"), str):
        raise RuntimeError("private control record readback was invalid")
    try:
        raw = base64.b64decode(value["content"], validate=False).decode("utf-8")
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("private control record decode failed") from exc
    if not isinstance(record, dict):
        raise RuntimeError("private control record was not an object")
    return record


def read_control_for_registration(registration: SourceRegistration) -> dict[str, Any]:
    control_repository = os.environ.get("CONTROL_REPOSITORY", "")
    control_token = os.environ.get("CONTROL_READ_TOKEN", "")
    if not control_repository:
        raise RuntimeError("CONTROL_REPOSITORY is missing")
    if not control_token:
        raise RuntimeError("CONTROL_READ_TOKEN is missing")
    return fetch_control_record(
        control_repository=control_repository,
        control_token=control_token,
        registration=registration,
    )


def queue_comment(
    registration: SourceRegistration,
    authority: SourceAuthority,
    *,
    issue_number: int,
    state: str,
    controller_run_id: str,
    source_sha: str = "",
    note: str = "",
) -> str:
    lines = [
        QUEUE_HEADING,
        "",
        f"- project: `{registration.project}`",
        f"- task_id: `{registration.task_id}`",
        f"- control_sha: `{registration.control_sha}`",
        f"- wake_issue: `{issue_number}`",
        f"- queue_state: `{state}`",
        f"- source_mode: `{authority.mode}`",
        f"- controller_run_id: `{controller_run_id}`",
        f"- workflow_file: `{registration.workflow_file}`",
    ]
    if source_sha:
        lines.append(f"- source_sha: `{source_sha}`")
    if authority.predecessor_issue is not None:
        lines.extend(
            [
                f"- predecessor_issue: `{authority.predecessor_issue}`",
                f"- predecessor_task_id: `{authority.predecessor_task_id}`",
                f"- predecessor_control_sha: `{authority.predecessor_control_sha}`",
                f"- predecessor_attempt: `{authority.predecessor_attempt}`",
            ]
        )
    if note:
        lines.append(f"- note: `{note}`")
    return "\n".join(lines)


def queue_claim_matches(
    comments: Sequence[str],
    *,
    registration: SourceRegistration,
    source_sha: str,
) -> bool:
    evidence = latest_queue_evidence(comments)
    if evidence is None or evidence.state != "DISPATCHED":
        return False
    fields = evidence.fields
    return (
        fields.get("project") == registration.project
        and fields.get("task_id") == registration.task_id
        and fields.get("control_sha") == registration.control_sha
        and fields.get("source_sha") == source_sha
        and fields.get("workflow_file") == registration.workflow_file
    )


def predecessor_issue(
    repository: str,
    owner: str,
    registration: SourceRegistration,
    authority: SourceAuthority,
) -> tuple[dict[str, Any], SourceRegistration]:
    number = authority.predecessor_issue
    if number is None:
        raise RuntimeError("predecessor-bound source authority has no predecessor issue")
    issue = issue_view(repository, number)
    predecessor = require_owner_registration(
        issue,
        owner=owner,
        expected_project=registration.project,
    )
    if predecessor.task_id != authority.predecessor_task_id:
        raise RuntimeError("predecessor task identity mismatch")
    if predecessor.control_sha != authority.predecessor_control_sha:
        raise RuntimeError("predecessor control SHA mismatch")
    return issue, predecessor


def resolve_predecessor_source(
    repository: str,
    owner: str,
    registration: SourceRegistration,
    authority: SourceAuthority,
) -> tuple[str, str]:
    issue, _predecessor = predecessor_issue(
        repository,
        owner,
        registration,
        authority,
    )
    comments = issue_comments(issue)
    result = terminal_result(comments)
    if result != "COMPLETED":
        return "", result or "NONTERMINAL"
    if str(issue.get("state") or "").upper() != "CLOSED":
        return "", "COMPLETED_BUT_OPEN"
    attempt = authority.predecessor_attempt
    if attempt is None:
        raise RuntimeError("predecessor attempt is unavailable")
    source_sha = completion_source_sha(
        comments,
        task_id=authority.predecessor_task_id,
        control_sha=authority.predecessor_control_sha,
        attempt=attempt,
    )
    if not source_sha:
        raise RuntimeError("predecessor COMPLETED evidence is not exact")
    return source_sha, "COMPLETED"


def dispatch_issue(
    repository: str,
    issue: Mapping[str, Any],
    registration: SourceRegistration,
    authority: SourceAuthority,
    *,
    source_sha: str,
    controller_run_id: str,
) -> None:
    issue_number = int(issue["number"])
    comments = issue_comments(issue)
    if queue_state(comments) == "DISPATCHED":
        return

    dispatched = queue_comment(
        registration,
        authority,
        issue_number=issue_number,
        state="DISPATCHED",
        controller_run_id=controller_run_id,
        source_sha=source_sha,
        note="runner-owned persistent queue dispatch",
    )
    post_comment(repository, issue_number, dispatched)
    try:
        run(
            [
                "gh",
                "workflow",
                "run",
                registration.workflow_file,
                "--repo",
                repository,
                "-f",
                f"wake_issue_number={issue_number}",
                "-f",
                f"task_id={registration.task_id}",
                "-f",
                f"control_sha={registration.control_sha}",
                "-f",
                f"source_sha={source_sha}",
            ]
        )
    except Exception:
        released = queue_comment(
            registration,
            authority,
            issue_number=issue_number,
            state="QUEUED",
            controller_run_id=controller_run_id,
            note="workflow dispatch failed; dispatch claim released for retry",
        )
        post_comment(repository, issue_number, released)
        raise


def reject_stale_exact_registration(
    args: argparse.Namespace,
    issue: Mapping[str, Any],
    registration: SourceRegistration,
    record: Mapping[str, Any],
    blocking_issue: int,
) -> None:
    attempt = record.get("attempt")
    if not isinstance(attempt, int) or attempt < 1:
        attempt = 1
    body = "\n".join(
        [
            "## Dispatcher v2 terminal evidence",
            "",
            f"- project: `{registration.project}`",
            f"- task_id: `{registration.task_id}`",
            f"- attempt: `{attempt}`",
            f"- control_sha: `{registration.control_sha}`",
            f"- source_sha: `{registration.exact_source_sha}`",
            f"- runner_run_id: `{args.controller_run_id}`",
            "- result: `FAILED`",
            "- recovery_state: `FINAL`",
            "- product_pr_cleanup: `NOT_APPLICABLE`",
            f"- blocking_predecessor_issue: `{blocking_issue}`",
            (
                "- next_action: register the successor with schema v2 "
                "predecessor_integrated_sha authority"
            ),
        ]
    )
    post_comment(args.repository, int(issue["number"]), body)
    close_issue(args.repository, int(issue["number"]))


def intake(args: argparse.Namespace) -> int:
    issue = issue_view(args.repository, args.issue_number)
    registration = require_owner_registration(
        issue,
        owner=args.owner,
        require_open=True,
    )
    comments = issue_comments(issue)
    if terminal_result(comments):
        print(json.dumps({"action": "TERMINAL", "issue": args.issue_number}))
        return 0
    if queue_state(comments) == "DISPATCHED":
        print(json.dumps({"action": "ALREADY_DISPATCHED", "issue": args.issue_number}))
        return 0

    record = read_control_for_registration(registration)
    authority = source_authority(record, registration)

    if authority.mode == "exact_source_sha":
        open_registrations = project_registrations(
            list_issues(args.repository, "open"),
            owner=args.owner,
            project=registration.project,
        )
        earlier = [
            item
            for item in open_registrations
            if int(item["number"]) < int(issue["number"])
        ]
        if earlier:
            blocking_issue = int(earlier[-1]["number"])
            reject_stale_exact_registration(
                args,
                issue,
                registration,
                record,
                blocking_issue,
            )
            print(
                json.dumps(
                    {
                        "action": "REJECTED_STALE_EXACT",
                        "issue": args.issue_number,
                        "blocking_issue": blocking_issue,
                    }
                )
            )
            return 0
        dispatch_issue(
            args.repository,
            issue,
            registration,
            authority,
            source_sha=authority.exact_source_sha,
            controller_run_id=args.controller_run_id,
        )
        print(json.dumps({"action": "DISPATCHED", "issue": args.issue_number}))
        return 0

    source_sha, predecessor_state = resolve_predecessor_source(
        args.repository,
        args.owner,
        registration,
        authority,
    )
    if source_sha:
        dispatch_issue(
            args.repository,
            issue,
            registration,
            authority,
            source_sha=source_sha,
            controller_run_id=args.controller_run_id,
        )
        print(json.dumps({"action": "DISPATCHED", "issue": args.issue_number}))
        return 0

    queued = queue_comment(
        registration,
        authority,
        issue_number=int(issue["number"]),
        state="QUEUED",
        controller_run_id=args.controller_run_id,
        note=f"waiting for exact predecessor terminal COMPLETED; current={predecessor_state}",
    )
    post_comment(args.repository, int(issue["number"]), queued)
    print(
        json.dumps(
            {
                "action": "QUEUED",
                "issue": args.issue_number,
                "predecessor": authority.predecessor_issue,
                "predecessor_state": predecessor_state,
            }
        )
    )
    return 0


def resolve_control(args: argparse.Namespace) -> int:
    issue = issue_view(args.repository, args.issue_number)
    registration = require_owner_registration(
        issue,
        owner=args.owner,
        expected_project=args.project,
        require_open=not args.allow_closed,
    )
    if registration.task_id != args.task_id:
        raise RuntimeError("queue issue task_id mismatch")
    if registration.control_sha != args.control_sha:
        raise RuntimeError("queue issue control_sha mismatch")
    raw = json.loads(Path(args.work_order_file).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("work order must be a JSON object")
    authority = source_authority(raw, registration)
    comments = issue_comments(issue)
    if not queue_claim_matches(
        comments,
        registration=registration,
        source_sha=args.source_sha,
    ):
        raise RuntimeError("live queue dispatch claim does not match requested source")

    if authority.mode == "exact_source_sha":
        if authority.exact_source_sha != args.source_sha:
            raise RuntimeError("exact source authority mismatch")
    else:
        resolved_source, predecessor_state = resolve_predecessor_source(
            args.repository,
            args.owner,
            registration,
            authority,
        )
        if predecessor_state != "COMPLETED" or resolved_source != args.source_sha:
            raise RuntimeError("predecessor completion does not authorize requested source")

    resolved = dict(raw)
    resolved["schema_version"] = 1
    resolved["source_base_sha"] = args.source_sha
    resolved["source_base_resolution"] = {
        "mode": authority.mode,
        "queue_issue": args.issue_number,
    }
    if authority.predecessor_issue is not None:
        resolved["source_base_resolution"].update(
            {
                "predecessor_wake_issue": authority.predecessor_issue,
                "predecessor_task_id": authority.predecessor_task_id,
                "predecessor_control_sha": authority.predecessor_control_sha,
                "predecessor_attempt": authority.predecessor_attempt,
            }
        )
    Path(args.output).write_text(
        json.dumps(resolved, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "action": "SOURCE_RESOLVED",
                "mode": authority.mode,
                "source_sha": args.source_sha,
            }
        )
    )
    return 0


def record_completed(args: argparse.Namespace) -> int:
    issue = issue_view(args.repository, args.issue_number)
    registration = require_owner_registration(
        issue,
        owner=args.owner,
        expected_project=args.project,
        require_open=True,
    )
    if registration.task_id != args.task_id:
        raise RuntimeError("completion task_id mismatch")
    if registration.control_sha != args.control_sha:
        raise RuntimeError("completion control_sha mismatch")
    if not queue_claim_matches(
        issue_comments(issue),
        registration=registration,
        source_sha=args.source_sha,
    ):
        raise RuntimeError("completed source task has no exact live queue dispatch claim")

    body = "\n".join(
        [
            QUEUE_TERMINAL_HEADING,
            "",
            f"- project: `{registration.project}`",
            f"- task_id: `{registration.task_id}`",
            f"- attempt: `{args.attempt}`",
            f"- control_sha: `{registration.control_sha}`",
            f"- source_sha: `{args.source_sha}`",
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


def locate_issue(args: argparse.Namespace) -> int:
    matches: list[int] = []
    issues = project_registrations(
        list_issues(args.repository, "all"),
        owner=args.owner,
        project=args.project,
    )
    for summary in issues:
        registration = parse_registration_title(str(summary.get("title") or ""))
        if registration is None:
            continue
        if not registration_matches_execution(
            registration,
            project=args.project,
            task_id=args.task_id,
            control_sha=args.control_sha,
        ):
            continue
        issue = issue_view(args.repository, int(summary["number"]))
        evidence = latest_queue_evidence(issue_comments(issue))
        if evidence is None:
            continue
        if (
            evidence.fields.get("source_sha") == args.source_sha
            and evidence.fields.get("queue_state") == "DISPATCHED"
        ):
            matches.append(int(summary["number"]))
    if len(matches) != 1:
        raise RuntimeError(f"expected one exact queue issue, found {len(matches)}")
    print(json.dumps({"issue_number": matches[0]}))
    return 0


def completed_issue_for_run(
    *,
    repository: str,
    owner: str,
    project: str,
    task_id: str,
    control_sha: str,
    source_sha: str,
    source_run_id: str,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    issues = project_registrations(
        list_issues(repository, "all"),
        owner=owner,
        project=project,
    )
    for summary in issues:
        registration = parse_registration_title(str(summary.get("title") or ""))
        if registration is None:
            continue
        if not registration_matches_execution(
            registration,
            project=project,
            task_id=task_id,
            control_sha=control_sha,
        ):
            continue
        issue = issue_view(repository, int(summary["number"]))
        if completion_matches_source_run(
            issue_comments(issue),
            source_run_id=source_run_id,
            source_sha=source_sha,
        ):
            matches.append(issue)
    if len(matches) != 1:
        raise RuntimeError(f"expected one completed queue issue, found {len(matches)}")
    completed = matches[0]
    if str(completed.get("state") or "").upper() != "CLOSED":
        raise RuntimeError("completed queue issue is not closed")
    return completed


def advance(args: argparse.Namespace) -> int:
    if args.conclusion != "success":
        print(json.dumps({"action": "STOPPED_NON_SUCCESS", "conclusion": args.conclusion}))
        return 0
    identity = parse_execution_title(args.wake_title)
    if identity is None:
        raise RuntimeError("source workflow display title is not exact")
    project, task_id, control_sha, source_sha = identity

    completed = completed_issue_for_run(
        repository=args.repository,
        owner=args.owner,
        project=project,
        task_id=task_id,
        control_sha=control_sha,
        source_sha=source_sha,
        source_run_id=args.source_run_id,
    )
    completed_number = int(completed["number"])

    open_issues = project_registrations(
        list_issues(args.repository, "open"),
        owner=args.owner,
        project=project,
    )
    candidate_summaries: list[Mapping[str, Any]] = []
    for item in open_issues:
        registration = parse_registration_title(str(item.get("title") or ""))
        if registration is not None and registration.predecessor_issue == completed_number:
            candidate_summaries.append(item)
    if not candidate_summaries:
        print(json.dumps({"action": "QUEUE_EMPTY", "project": project}))
        return 0
    if len(candidate_summaries) != 1:
        raise RuntimeError("multiple queued successors target the same predecessor")

    candidate = issue_view(args.repository, int(candidate_summaries[0]["number"]))
    registration = require_owner_registration(
        candidate,
        owner=args.owner,
        expected_project=project,
        require_open=True,
    )
    if queue_state(issue_comments(candidate)) == "DISPATCHED":
        print(json.dumps({"action": "NEXT_ALREADY_DISPATCHED", "issue": candidate["number"]}))
        return 0

    record = read_control_for_registration(registration)
    authority = source_authority(record, registration)
    if authority.mode != "predecessor_integrated_sha":
        raise RuntimeError("queued successor does not use predecessor-bound source authority")
    if authority.predecessor_issue != completed_number:
        raise RuntimeError("queued successor predecessor lineage mismatch")
    resolved_source, predecessor_state = resolve_predecessor_source(
        args.repository,
        args.owner,
        registration,
        authority,
    )
    if predecessor_state != "COMPLETED" or not resolved_source:
        raise RuntimeError("queued successor predecessor is not exactly COMPLETED")

    dispatch_issue(
        args.repository,
        candidate,
        registration,
        authority,
        source_sha=resolved_source,
        controller_run_id=args.controller_run_id,
    )
    print(
        json.dumps(
            {
                "action": "DISPATCHED_NEXT",
                "completed_issue": completed_number,
                "next_issue": candidate["number"],
                "source_sha": resolved_source,
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

    resolve_parser = sub.add_parser("resolve-control")
    resolve_parser.add_argument("--repository", required=True)
    resolve_parser.add_argument("--owner", required=True)
    resolve_parser.add_argument("--issue-number", type=int, required=True)
    resolve_parser.add_argument("--project", required=True)
    resolve_parser.add_argument("--task-id", required=True)
    resolve_parser.add_argument("--control-sha", required=True)
    resolve_parser.add_argument("--source-sha", required=True)
    resolve_parser.add_argument("--work-order-file", required=True)
    resolve_parser.add_argument("--output", required=True)
    resolve_parser.add_argument("--allow-closed", action="store_true")
    resolve_parser.set_defaults(func=resolve_control)

    completed_parser = sub.add_parser("record-completed")
    completed_parser.add_argument("--repository", required=True)
    completed_parser.add_argument("--owner", required=True)
    completed_parser.add_argument("--issue-number", type=int, required=True)
    completed_parser.add_argument("--project", required=True)
    completed_parser.add_argument("--task-id", required=True)
    completed_parser.add_argument("--control-sha", required=True)
    completed_parser.add_argument("--source-sha", required=True)
    completed_parser.add_argument("--attempt", required=True)
    completed_parser.add_argument("--source-run-id", required=True)
    completed_parser.add_argument("--product-pr", required=True)
    completed_parser.add_argument("--integrated-sha", required=True)
    completed_parser.add_argument("--validation-run", required=True)
    completed_parser.set_defaults(func=record_completed)

    locate_parser = sub.add_parser("locate")
    locate_parser.add_argument("--repository", required=True)
    locate_parser.add_argument("--owner", required=True)
    locate_parser.add_argument("--project", required=True)
    locate_parser.add_argument("--task-id", required=True)
    locate_parser.add_argument("--control-sha", required=True)
    locate_parser.add_argument("--source-sha", required=True)
    locate_parser.set_defaults(func=locate_issue)

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
