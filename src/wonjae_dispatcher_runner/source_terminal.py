"""Exact source terminal reconciliation; never dispatches or integrates product work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .gmail_notifications import Notification
from .guards import require_sha
from .source_checkpoint import (
    CHECKPOINT_HEADING,
    SourceCheckpointError,
    terminal_repair_head,
)
from .source_pr_lifecycle import (
    SourceIdentity,
    parse_fields,
    parse_pr_metadata,
    terminalize_exact_pr,
)
from .source_queue import WORKFLOW_FILES, parse_execution_title, parse_registration_title

TERMINAL_HEADING = "## Dispatcher v2 terminal evidence"
NON_SUCCESS = frozenset({
    "failure", "cancelled", "timed_out", "action_required", "neutral", "stale", "startup_failure"
})


class SourceTerminalError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalizerRequest:
    run_id: int
    wake_issue: int
    product_pr: int | None = None
    last_head: str = ""
    owner_reconciliation: bool = False


def trusted_comments(issue: dict[str, Any], owner: str) -> list[dict[str, Any]]:
    return [comment for comment in issue.get("comments", [])
            if (comment.get("author") or comment.get("user") or {}).get("login")
            in {owner, "github-actions", "github-actions[bot]"}]


def verify_authority(
    request: FinalizerRequest, run: dict[str, Any], jobs: list[dict[str, Any]],
    issue: dict[str, Any], record: dict[str, Any], runner_repository: str, owner: str,
) -> tuple[SourceIdentity, int, str, list[dict[str, Any]]]:
    parsed = parse_execution_title(str(run.get("display_title") or ""))
    if parsed is None:
        raise SourceTerminalError("run has no exact source execution identity")
    project, task, control, source = parsed
    expected_path = f".github/workflows/{WORKFLOW_FILES[project]}"
    if (run.get("id") != request.run_id or run.get("status") != "completed"
            or run.get("conclusion") not in NON_SUCCESS or run.get("event") != "workflow_dispatch"
            or run.get("path") != expected_path or run.get("head_branch") != "main"
            or (run.get("repository") or {}).get("full_name") != runner_repository):
        raise SourceTerminalError("source run id/status/path/branch/repository mismatch")
    require_sha(str(run.get("head_sha") or ""))
    product_jobs = [job for job in jobs if job.get("name") == f"{project.lower()}-product"]
    if len(product_jobs) != 1 or product_jobs[0].get("status") != "completed":
        raise SourceTerminalError("exact source product job is not terminal")
    if product_jobs[0].get("conclusion") not in NON_SUCCESS:
        raise SourceTerminalError("non-success workflow is not a non-success source product job")
    registration = parse_registration_title(str(issue.get("title") or ""))
    author = (issue.get("author") or issue.get("user") or {}).get("login")
    if (issue.get("number") != request.wake_issue or author != owner or registration is None
            or registration.project != project or registration.task_id != task
            or registration.control_sha != control):
        raise SourceTerminalError("wake project/task/control/owner/number mismatch")
    expected_record = {"project": project, "task_id": task,
                       "repository": f"{owner}/{project}", "target_branch": "cloud-dev-v1",
                       "source_base_sha": source}
    if any(record.get(key) != value for key, value in expected_record.items()):
        raise SourceTerminalError("exact private control/source/routing mismatch")
    attempt, revision = record.get("attempt"), record.get("revision")
    if (type(attempt) is not int or attempt < 1 or type(revision) is not int or revision < 1):
        raise SourceTerminalError("private attempt/revision is invalid")
    comments = trusted_comments(issue, owner)
    claims = [parse_fields(str(comment.get("body") or "")) for comment in comments
              if "## Dispatcher v2 source queue evidence" in str(comment.get("body") or "")
              and (comment.get("author") or comment.get("user") or {}).get("login")
              in {"github-actions", "github-actions[bot]"}]
    expected_claim = {"project": project, "task_id": task, "control_sha": control,
                      "source_sha": source, "wake_issue": str(request.wake_issue),
                      "workflow_file": WORKFLOW_FILES[project], "queue_state": "DISPATCHED"}
    if not claims or any(claims[-1].get(key) != value for key, value in expected_claim.items()):
        raise SourceTerminalError("exact trusted dispatch claim mismatch")
    return (SourceIdentity(project, record["repository"], record["target_branch"], task,
                           attempt, control, source, str(request.run_id)), revision,
            expected_path, comments)


def reconcile_source_terminal(
    client: Any, notifications: Any, request: FinalizerRequest, record: dict[str, Any],
    runner_repository: str, owner: str,
) -> dict[str, Any]:
    run = client.get_run(request.run_id)
    issue = client.get_issue(request.wake_issue)
    identity, revision, path, comments = verify_authority(
        request, run, client.get_jobs(request.run_id), issue, record, runner_repository, owner
    )
    binding = {"project": identity.project, "task_id": identity.task_id,
               "attempt": str(identity.attempt), "revision": str(revision),
               "control_sha": identity.control_sha, "source_sha": identity.source_sha,
               "runner_run_id": identity.runner_run_id}
    terminals = [comment for comment in comments
                 if TERMINAL_HEADING in str(comment.get("body") or "")
                 and parse_fields(str(comment.get("body") or "")).get("runner_run_id")
                 == identity.runner_run_id]
    if len(terminals) > 1:
        raise SourceTerminalError("duplicate terminal checkpoints require exact inspection")
    terminal = terminals[0] if terminals else None
    previous = parse_fields(str(terminal.get("body") or "")) if terminal else {}
    if terminal and (any(previous.get(key) != value for key, value in binding.items())
                     or previous.get("recovery_state") not in {"FINAL", "FINAL_GUARD"}
                     or previous.get("result") not in {"FAILED", "MANUAL_REQUIRED", "CANCELLED"}):
        raise SourceTerminalError("existing terminal evidence identity/result mismatch")

    checkpoints = [parse_fields(str(comment.get("body") or "")) for comment in comments
                   if CHECKPOINT_HEADING in str(comment.get("body") or "")
                   and (comment.get("author") or comment.get("user") or {}).get("login")
                   in {"github-actions", "github-actions[bot]"}
                   and parse_fields(str(comment.get("body") or "")).get("runner_run_id")
                   == identity.runner_run_id]
    if len(checkpoints) > 1:
        raise SourceTerminalError("duplicate source run checkpoints")
    checkpoint = checkpoints[-1] if checkpoints else {}
    if checkpoint:
        expected = {**binding, "wake_issue": str(request.wake_issue),
                    "repository": identity.repository, "target_branch": identity.target_branch,
                    "runner_workflow_path": path, "runner_head_sha": str(run["head_sha"])}
        if any(checkpoint.get(key) != value for key, value in expected.items()):
            raise SourceTerminalError("source run checkpoint mismatch")
        optional_identity = {"product_job_branch": identity.expected_branch,
                             "product_head_repository": identity.repository}
        if any(checkpoint.get(key, value) != value for key, value in optional_identity.items()):
            raise SourceTerminalError("source checkpoint job branch/head repository mismatch")
    if request.owner_reconciliation:
        pr_number, head = request.product_pr, request.last_head
        if checkpoint and (checkpoint.get("product_pr") != str(pr_number)
                           or checkpoint.get("last_pr_head") != head):
            raise SourceTerminalError("Owner reconciliation differs from trusted checkpoint")
    elif checkpoint:
        pr_number, head = int(checkpoint["product_pr"]), checkpoint["last_pr_head"]
    elif terminal and previous.get("product_pr"):
        pr_number, head = int(previous["product_pr"]), previous.get("last_pr_head", "")
    else:
        pr_number, head = None, ""
        product = next(job for job in client.get_jobs(request.run_id)
                       if job.get("name") == f"{identity.project.lower()}-product")
        steps = product.get("steps") or []
        if any("STARTED ·" in str(step.get("name")) and step.get("conclusion") == "success"
               for step in steps):
            raise SourceTerminalError("started source lacks an exact Product PR checkpoint")
    if request.product_pr is not None and request.product_pr != pr_number:
        raise SourceTerminalError("explicit Product PR mismatch")
    if checkpoint and pr_number is not None:
        try:
            head = terminal_repair_head(client, identity, checkpoint, str(revision))
        except SourceCheckpointError as exc:
            raise SourceTerminalError(str(exc)) from exc
    if request.last_head and request.last_head != head:
        raise SourceTerminalError("explicit PR head mismatch")

    if pr_number is not None:
        require_sha(head)
        pr = client.get_pr(identity.repository, pr_number)
        metadata = parse_pr_metadata(str(pr.get("body") or ""))
        expected_pr = {"project": identity.project, "task_id": identity.task_id,
                       "attempt": str(identity.attempt), "revision": str(revision),
                       "control_sha": identity.control_sha, "source_base_sha": identity.source_sha,
                       "repository": identity.repository, "target_branch": identity.target_branch}
        permitted_heads = {head}
        if checkpoint.get("pending_repair_head") and head == checkpoint["pending_repair_head"]:
            permitted_heads.add(checkpoint["last_pr_head"])
        elif checkpoint.get("checkpoint_state") == "READY" and checkpoint.get("previous_pr_head"):
            permitted_heads.add(checkpoint["previous_pr_head"])
        if (int(pr.get("number") or 0) != pr_number
                or any(metadata.get(key) != value for key, value in expected_pr.items())
                or pr.get("headRefOid") not in permitted_heads
                or pr.get("headRefName") != identity.expected_branch
                or (pr.get("headRepository") or {}).get("nameWithOwner") != identity.repository
                or pr.get("baseRefName") != identity.target_branch or pr.get("mergedAt")):
            raise SourceTerminalError("exact Product PR metadata/head/branch/unmerged mismatch")

    result = previous.get("result") or {"cancelled": "CANCELLED",
                                        "action_required": "MANUAL_REQUIRED"}.get(
                                            str(run["conclusion"]), "FAILED")
    fields = {**binding, "repository": identity.repository, "target_branch": identity.target_branch,
              "wake_issue": str(request.wake_issue), "runner_workflow_path": path,
              "runner_conclusion": str(run["conclusion"]), "result": result,
              "recovery_state": "FINAL", "runtime_device_physical": "NOT_INFERRED"}
    if pr_number is not None:
        fields.update(product_pr=str(pr_number), last_pr_head=head)
    body = _markdown(fields)
    if pr_number is not None:
        cleanup = terminalize_exact_pr(client, identity, body, result, pr_number)
        fields.update(product_pr_cleanup=(previous.get("product_pr_cleanup")
                      if cleanup.status == "ALREADY_CLOSED" and previous.get("product_pr_cleanup")
                      in {"CLOSED", "ALREADY_CLOSED"} else cleanup.status),
                      product_pr_cleanup_detail=(previous.get("product_pr_cleanup_detail")
                      if cleanup.status == "ALREADY_CLOSED" and previous.get("product_pr_cleanup")
                      in {"CLOSED", "ALREADY_CLOSED"} else cleanup.detail))
        fields["next_action"] = cleanup.next_action
    else:
        fields.update(product_pr_cleanup="NOT_APPLICABLE",
                      product_pr_cleanup_detail="no Product PR established before STARTED",
                      next_action="inspect exact terminal evidence before any separate retry")
    fields["terminal_cause"] = "source product job ended " + str(run["conclusion"])
    fields["notification_result"] = previous.get("notification_result", "PENDING")
    fields["started_cleanup"] = previous.get("started_cleanup", "PENDING")
    # Preserve the authoritative source result before network notification; if that
    # transport fails, GitHub still shows a terminal checkpoint and exact residue.
    body = _markdown(fields)
    comment_id = int(terminal["id"]) if terminal else client.add_comment(request.wake_issue, body)
    if terminal and previous != fields:
        client.update_comment(comment_id, body)
    if str(issue.get("state") or "").upper() != "CLOSED":
        client.close_issue(request.wake_issue)
    if str(client.get_issue(request.wake_issue).get("state") or "").upper() != "CLOSED":
        raise SourceTerminalError("wake close readback failed")
    try:
        if notifications is None:
            raise SourceTerminalError("native Gmail is unavailable")
        outcome = notifications.deliver(Notification(
            identity.project, identity.task_id, identity.attempt, result,
            profile=str(record.get("profile") or ""), control_sha=identity.control_sha,
            source_sha=identity.source_sha,
            product_pr=(f"https://github.com/{identity.repository}/pull/{pr_number}"
                        if pr_number is not None else ""), exact_head_sha=head,
            smallest_next_action=fields["next_action"],
        ))
        fields.update(notification_result="DELIVERED", started_cleanup="VERIFIED_OR_NOT_PRESENT")
    except Exception as exc:
        fields.update(notification_result="RESIDUE", started_cleanup="UNVERIFIED",
                      notification_residue=type(exc).__name__)
        outcome = {"sent": False}
    updated = _markdown(fields)
    if updated != body:
        client.update_comment(comment_id, updated)
    return {"result": result, "product_pr": pr_number, "pr_cleanup": fields["product_pr_cleanup"],
            "notification_result": fields["notification_result"], "sent": outcome["sent"]}


def _markdown(fields: dict[str, str]) -> str:
    return TERMINAL_HEADING + "\n\n" + "\n".join(
        f"- {key}: `{value.replace('`', '').replace(chr(10), ' ')}`"
        for key, value in fields.items()
    ) + "\n"
