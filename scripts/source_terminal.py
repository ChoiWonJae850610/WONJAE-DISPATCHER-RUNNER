from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts import source_queue as queue  # noqa: E402
from wonjae_dispatcher_runner.gmail_notifications import (  # noqa: E402
    GmailClient,
    NotificationService,
)
from wonjae_dispatcher_runner.source_pr_lifecycle import (  # noqa: E402
    GhClient,
    SourceIdentity,
    parse_fields,
    parse_pr_metadata,
)
from wonjae_dispatcher_runner.source_queue import (  # noqa: E402
    WORKFLOW_FILES,
    parse_execution_title,
)
from wonjae_dispatcher_runner.source_terminal import (  # noqa: E402
    CHECKPOINT_HEADING,
    NON_SUCCESS,
    FinalizerRequest,
    SourceTerminalError,
    reconcile_source_terminal,
    trusted_comments,
)


class Client(GhClient):
    def __init__(self, runner_repository: str, product_token: str) -> None:
        super().__init__(product_token, os.environ["GH_TOKEN"])
        self.runner_repository = runner_repository

    def get_run(self, number: int) -> dict:
        return queue.gh_json(["gh", "api", f"repos/{self.runner_repository}/actions/runs/{number}"])

    def get_jobs(self, number: int) -> list[dict]:
        value = queue.gh_json([
            "gh", "api", f"repos/{self.runner_repository}/actions/runs/{number}/jobs?per_page=100"
        ])
        return value["jobs"]

    def get_issue(self, number: int) -> dict:
        issue = queue.issue_view(self.runner_repository, number)
        # REST comment ids are needed for update-in-place idempotency; read all pages.
        pages = queue.gh_json([
            "gh", "api", "--paginate", "--slurp",
            f"repos/{self.runner_repository}/issues/{number}/comments?per_page=100",
        ])
        issue["comments"] = [comment for page in pages for comment in page]
        return issue

    def add_comment(self, number: int, body: str) -> int:
        result = queue.gh_json([
            "gh", "api", "--method", "POST",
            f"repos/{self.runner_repository}/issues/{number}/comments", "-f", f"body={body}",
        ])
        return int(result["id"])

    def update_comment(self, number: int, body: str) -> None:
        queue.run(["gh", "api", "--method", "PATCH",
                   f"repos/{self.runner_repository}/issues/comments/{number}",
                   "-f", f"body={body}"])

    def close_issue(self, number: int) -> None:
        queue.close_issue(self.runner_repository, number)


def positive(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise SourceTerminalError("identity number must be a positive integer")
    return int(value)


def select() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    name = os.environ["GITHUB_EVENT_NAME"]
    repository = os.environ["GITHUB_REPOSITORY"]
    owner = os.environ["GITHUB_REPOSITORY_OWNER"]
    wake, pr, head, owner_reconciliation = "", "", "", "false"
    if name == "workflow_run":
        run_id = positive(str(event["workflow_run"]["id"]))
    elif name == "workflow_dispatch":
        run_id = positive(str(event["inputs"]["source_run_id"]))
        wake = str(event["inputs"].get("wake_issue_number") or "")
    elif name == "issue_comment":
        if event["comment"]["user"]["login"] != owner or event["issue"]["user"]["login"] != owner:
            raise SourceTerminalError("reconciliation comment and wake must be Owner-authored")
        match = re.fullmatch(
            r"\[SOURCE-TERMINAL-RECONCILE\] run=([1-9][0-9]*)"
            r"(?: pr=([1-9][0-9]*) head=([0-9a-f]{40}))?", event["comment"]["body"].strip()
        )
        if match is None:
            raise SourceTerminalError("invalid exact reconciliation command")
        run_id = positive(match[1])
        pr, head = match[2] or "", match[3] or ""
        wake, owner_reconciliation = str(event["issue"]["number"]), "true"
    else:
        raise SourceTerminalError("unsupported source finalizer event")
    run = None
    for index in range(7):
        run = queue.gh_json(["gh", "api", f"repos/{repository}/actions/runs/{run_id}"])
        if run.get("status") == "completed":
            break
        if index == 6:
            raise SourceTerminalError("exact source run is not terminal; no mutation authorized")
        time.sleep(5)  # Only the trusted handoff finalizer, bounded to 30 seconds.
    parsed = parse_execution_title(str(run.get("display_title") or ""))
    if parsed is None:
        raise SourceTerminalError("source run has no exact execution title")
    project, task, control, source = parsed
    if (run.get("path") != f".github/workflows/{WORKFLOW_FILES[project]}"
            or run.get("id") != run_id or run.get("status") != "completed"
            or run.get("conclusion") not in NON_SUCCESS or run.get("event") != "workflow_dispatch"
            or run.get("head_branch") != "main"
            or (run.get("repository") or {}).get("full_name") != repository):
        raise SourceTerminalError("source runner workflow/run/status/repository mismatch")
    if wake:
        positive(wake)
    else:
        args = argparse.Namespace(repository=repository, owner=owner, project=project,
                                  task_id=task, control_sha=control, source_sha=source)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            queue.locate_issue(args)
        wake = str(json.loads(captured.getvalue())["issue_number"])
    outputs = {"project": project, "source_run_id": str(run_id), "wake_issue_number": wake,
               "product_pr": pr, "last_head": head, "owner_reconciliation": owner_reconciliation}
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        handle.write("".join(f"{key}={value}\n" for key, value in outputs.items()))
    return 0


def checkpoint() -> int:
    record = json.loads(Path(os.environ["WORK_ORDER_FILE"]).read_text())
    repository, owner = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REPOSITORY_OWNER"]
    client = Client(repository, os.environ["PRODUCT_GH_TOKEN"])
    run_id, wake = positive(os.environ["GITHUB_RUN_ID"]), positive(os.environ["ISSUE_NUMBER"])
    run = client.get_run(run_id)
    parsed = parse_execution_title(str(run.get("display_title") or ""))
    expected = (record["project"], record["task_id"], os.environ["CONTROL_SHA"],
                record["source_base_sha"])
    expected_path = f".github/workflows/{WORKFLOW_FILES[record['project']]}"
    if (parsed != expected or run.get("path") != expected_path or run.get("id") != run_id
            or run.get("head_branch") != "main"
            or (run.get("repository") or {}).get("full_name") != repository):
        raise SourceTerminalError("checkpoint source run binding mismatch")
    pr_number, head = positive(os.environ["PILOT_PR_NUMBER"]), os.environ["PILOT_HEAD_SHA"]
    pr = client.get_pr(record["repository"], pr_number)
    metadata = parse_pr_metadata(str(pr.get("body") or ""))
    identity = SourceIdentity(record["project"], record["repository"], record["target_branch"],
                              record["task_id"], record["attempt"], os.environ["CONTROL_SHA"],
                              record["source_base_sha"], str(run_id))
    expected_metadata = {key: str(record[key]) for key in (
        "project", "task_id", "attempt", "revision", "repository", "target_branch",
        "source_base_sha"
    )}
    expected_metadata["control_sha"] = os.environ["CONTROL_SHA"]
    if (pr.get("headRefOid") != head or pr.get("mergedAt") or pr.get("number") != pr_number
            or pr.get("headRefName") != identity.expected_branch
            or pr.get("baseRefName") != identity.target_branch
            or any(metadata.get(key) != value for key, value in expected_metadata.items())):
        raise SourceTerminalError("checkpoint PR metadata/head/branch/unmerged mismatch")
    fields = {"project": record["project"], "task_id": record["task_id"],
              "attempt": str(record["attempt"]), "revision": str(record["revision"]),
              "repository": record["repository"], "target_branch": record["target_branch"],
              "control_sha": os.environ["CONTROL_SHA"], "source_sha": record["source_base_sha"],
              "wake_issue": str(wake), "runner_run_id": str(run_id),
              "runner_head_sha": run["head_sha"], "runner_workflow_path": run["path"],
              "product_pr": str(pr_number), "last_pr_head": head}
    body = (CHECKPOINT_HEADING + "\n\n"
            + "\n".join(f"- {k}: `{v}`" for k, v in fields.items()) + "\n")
    prior = [comment for comment in trusted_comments(client.get_issue(wake), owner)
             if CHECKPOINT_HEADING in comment["body"]
             and parse_fields(comment["body"]).get("runner_run_id") == str(run_id)
             and comment["user"]["login"] in {"github-actions", "github-actions[bot]"}]
    if len(prior) > 1:
        raise SourceTerminalError("duplicate source run checkpoints")
    if prior:
        if prior[0]["body"] != body:
            client.update_comment(int(prior[0]["id"]), body)
    else:
        client.add_comment(wake, body)
    return 0


def finalize(args: argparse.Namespace) -> int:
    repository, owner = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REPOSITORY_OWNER"]
    client = Client(repository, os.environ["PRODUCT_GH_TOKEN"])
    run = client.get_run(args.source_run_id)
    parsed = parse_execution_title(str(run.get("display_title") or ""))
    if parsed is None or parsed[0] != args.project:
        raise SourceTerminalError("finalizer project/run mismatch")
    project, task, control, source = parsed
    issue = client.get_issue(args.wake_issue_number)
    registration = queue.require_owner_registration(
        issue, owner=owner, expected_project=project, require_open=False
    )
    record = queue.read_control_for_registration(registration)
    with tempfile.TemporaryDirectory(
        prefix="source-terminal-", dir=os.environ.get("RUNNER_TEMP")
    ) as tmp:
        raw, resolved = Path(tmp) / "raw.json", Path(tmp) / "resolved.json"
        raw.write_text(json.dumps(record))
        queue.resolve_control(argparse.Namespace(
            repository=repository, owner=owner, issue_number=args.wake_issue_number,
            project=project, task_id=task, control_sha=control, source_sha=source,
            work_order_file=str(raw), output=str(resolved), allow_closed=True,
        ))
        record = json.loads(resolved.read_text())
    notifications = None
    if (os.environ.get("GMAIL_ENABLED") == "true" and os.environ.get("GMAIL_USERNAME")
            and os.environ.get("GMAIL_APP_PASSWORD")):
        notifications = NotificationService(GmailClient(
            os.environ["GMAIL_USERNAME"], os.environ["GMAIL_APP_PASSWORD"]
        ))
    outcome = reconcile_source_terminal(client, notifications, FinalizerRequest(
        args.source_run_id, args.wake_issue_number, args.product_pr, args.last_head,
        args.owner_reconciliation,
    ), record, repository, owner)
    print(json.dumps(outcome))
    if outcome["pr_cleanup"] == "RESIDUE" or outcome["notification_result"] != "DELIVERED":
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("select")
    sub.add_parser("checkpoint")
    final = sub.add_parser("finalize")
    final.add_argument("--project", required=True, choices=list(WORKFLOW_FILES))
    final.add_argument("--source-run-id", required=True, type=int)
    final.add_argument("--wake-issue-number", required=True, type=int)
    final.add_argument("--product-pr", type=int)
    final.add_argument("--last-head", default="")
    final.add_argument("--owner-reconciliation", action="store_true")
    args = parser.parse_args()
    if args.command == "select":
        return select()
    if args.command == "checkpoint":
        return checkpoint()
    return finalize(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # gh/transport exceptions can contain credentials or private context.
        detail = str(exc) if isinstance(exc, SourceTerminalError) else type(exc).__name__
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as handle:
                handle.write("Source terminal finalizer residue: " + detail + "\n")
        print("SOURCE_TERMINAL_FINALIZER_RESIDUE=" + detail, file=sys.stderr)
        raise SystemExit(1) from None
