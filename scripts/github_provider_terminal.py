"""Exact GitHub provider reconciliation; bounded waiting belongs only to Actions.

Provider reads and runner issue writes deliberately use different credentials.
No dispatch API exists in this module. A persisted terminal is also a mail retry
checkpoint: Notification-Key readback prevents another RESULT delivery.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wonjae_dispatcher_runner.gmail_notifications import (  # noqa: E402
    GmailClient,
    Notification,
    NotificationService,
)

HEADING = "## Dispatcher v2 provider completion evidence"
QUEUED = "## Dispatcher v2 provider QUEUED evidence"
FIELDS = ("task_id", "attempt", "revision", "control_sha", "source_sha", "action")
TERMINALS = {"PASS": "COMPLETED", "FAILED": "FAILED", "MANUAL_REQUIRED": "MANUAL_REQUIRED"}


class IdentityError(ValueError):
    pass


def fields(body):
    return dict(re.findall(r"^- ([a-z_]+): `([^`]+)`$", body, re.MULTILINE))


def exact_comments(comments, heading, identity):
    for comment in comments:
        if comment.get("user", {}).get("login") != "github-actions[bot]":
            continue
        body = comment.get("body", "")
        value = fields(body)
        if body.startswith(heading) and all(value.get(k) == v for k, v in identity.items()):
            yield value


def validate_run(run, *, repository, workflow_id, workflow_file, run_id, source_sha):
    expected_path = f".github/workflows/{workflow_file}"
    if (
        str(run.get("id")) != str(run_id)
        or run.get("repository", {}).get("full_name") != repository
        or run.get("head_repository", {}).get("full_name") != repository
        or run.get("workflow_id") != workflow_id
        or str(run.get("path", "")).split("@", 1)[0] != expected_path
        or run.get("head_sha") != source_sha
        or run.get("event") != "workflow_dispatch"
    ):
        raise IdentityError("exact provider repository/workflow/run/source identity mismatch")
    if run.get("status") != "completed":
        if run.get("status") not in {"queued", "in_progress", "waiting", "pending", "requested"}:
            raise IdentityError("unrecognized provider run state")
        return "QUEUED"
    conclusion = run.get("conclusion")
    if conclusion == "success":
        return "PASS"
    if conclusion in {"failure", "cancelled", "timed_out", "startup_failure"}:
        return "FAILED"
    # Neutral/skipped/stale/unknown outcomes cannot be accepted as successful work.
    return "MANUAL_REQUIRED"


def bounded_result(read, *, seconds, clock=time.monotonic, sleep=time.sleep):
    if not 0 <= seconds <= 600:
        raise ValueError("GitHub exact-run wait must be bounded to 0..600 seconds")
    deadline = clock() + seconds
    while True:
        result = read()
        if result != "QUEUED":
            return result
        remaining = deadline - clock()
        if remaining <= 0:
            return "QUEUED"
        sleep(min(15, remaining))


def api(path, *, token, method="GET", payload=None):
    command = ["gh", "api", "--method", method, path]
    if payload is not None:
        command += ["--input", "-"]
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    output = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        env=env,
        check=True,
        text=True,
        capture_output=True,
        timeout=45,
    ).stdout
    return json.loads(output) if output.strip() else None


def comments_for(repository, issue, token):
    comments = []
    for page in range(1, 101):
        batch = api(
            f"repos/{repository}/issues/{issue}/comments?per_page=100&page={page}", token=token
        )
        comments.extend(batch)
        if len(batch) < 100:
            return comments
    raise ValueError("provider comments exceeded bounded pagination")


def finalize(result, *, identity, run_id, comments, write_comment, close, notify):
    bound = {**identity, "provider_run_id": str(run_id)}
    previous = list(exact_comments(comments, HEADING, bound))
    if previous:
        recorded = previous[-1].get("result")
        if recorded not in TERMINALS:
            raise IdentityError("unknown persisted provider terminal")
        result = recorded
    else:
        body = "\n".join(
            [
                HEADING,
                "",
                *(f"- {k}: `{v}`" for k, v in bound.items()),
                f"- result: `{result}`",
                "- device/physical: `NOT_RUN`",
            ]
        )
        write_comment(body)
    # close() checks readback, so a late status request never repeats issue close.
    close()
    notify(TERMINALS[result])
    return result


def launch_gate(comments, identity):
    if list(exact_comments(comments, QUEUED, identity)):
        return "reconcile"
    if list(
        exact_comments(comments, "## Dispatcher v2 provider STARTED evidence", identity)
    ) or list(exact_comments(comments, HEADING, identity)):
        raise IdentityError(
            "provider launch was already attempted; never resubmit without exact run"
        )
    return "ready"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--check-launch", action="store_true")
    args = parser.parse_args()
    env = os.environ
    identity = {k: env[k.upper() if k != "action" else "PROVIDER_ACTION"] for k in FIELDS}
    if identity["action"] != "github_workflow_dispatch":
        raise ValueError("bounded wait is restricted to github_workflow_dispatch")
    repository, issue = env["GITHUB_REPOSITORY"], env["ISSUE_NUMBER"]
    runner_token = env["RUNNER_GH_TOKEN"]
    provider_token = env["GH_TOKEN"]
    comments = comments_for(repository, issue, runner_token)
    if args.check_launch:
        gate = launch_gate(comments, identity)
        with open(env["GITHUB_ENV"], "a", encoding="utf-8") as handle:
            handle.write(f"PROVIDER_GATE={gate}\n")
        return 0
    queued = list(exact_comments(comments, QUEUED, identity))
    if not queued:
        raise IdentityError("no exact persisted provider run handoff")
    run_ids = {value.get("provider_run_id") for value in queued}
    if len(run_ids) != 1 or not next(iter(run_ids), ""):
        raise IdentityError("ambiguous provider run handoff")
    run_id = next(iter(run_ids))
    if env.get("PROVIDER_GITHUB_RUN_ID", run_id) != run_id:
        raise IdentityError("captured provider run differs from persisted handoff")
    bound = {**identity, "provider_run_id": run_id}
    previous = list(exact_comments(comments, HEADING, bound))
    if previous:
        result = previous[-1]["result"]
    else:
        product = env["PRODUCT_REPOSITORY"]
        workflow_file = env["PROVIDER_WORKFLOW_FILE"]
        workflow = api(f"repos/{product}/actions/workflows/{workflow_file}", token=provider_token)
        if workflow.get("path") != f".github/workflows/{workflow_file}":
            raise IdentityError("provider workflow metadata mismatch")

        def read():
            value = api(f"repos/{product}/actions/runs/{run_id}", token=provider_token)
            return validate_run(
                value,
                repository=product,
                workflow_id=workflow["id"],
                workflow_file=workflow_file,
                run_id=run_id,
                source_sha=identity["source_sha"],
            )

        # Identity errors propagate without accepting or closing the mismatched run.
        result = bounded_result(read, seconds=args.wait_seconds)
    if result == "QUEUED":
        print(f"PROVIDER_QUEUED exact-run={run_id}; status reconciliation remains available")
        return 0

    def close():
        current = api(f"repos/{repository}/issues/{issue}", token=runner_token)
        if current["state"] != "closed":
            api(
                f"repos/{repository}/issues/{issue}",
                token=runner_token,
                method="PATCH",
                payload={"state": "closed", "state_reason": "completed"},
            )

    def notify(status):
        if env.get("GMAIL_ENABLED", "false").lower() != "true":
            return
        note = Notification(
            project=env["PROJECT"],
            task_id=identity["task_id"],
            attempt=int(identity["attempt"]),
            status=status,
            profile=env.get("PROFILE", ""),
            control_sha=identity["control_sha"],
            source_sha=identity["source_sha"],
            integration_evidence=f"provider-action:github_workflow_dispatch provider-run:{run_id}",
        )
        NotificationService(GmailClient(env["GMAIL_USERNAME"], env["GMAIL_APP_PASSWORD"])).deliver(
            note
        )

    finalize(
        result,
        identity=identity,
        run_id=run_id,
        comments=comments,
        write_comment=lambda body: api(
            f"repos/{repository}/issues/{issue}/comments",
            token=runner_token,
            method="POST",
            payload={"body": body},
        ),
        close=close,
        notify=notify,
    )
    print(f"PROVIDER_TERMINAL exact-run={run_id} result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
