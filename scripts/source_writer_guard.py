"""Work-boundary hooks for the existing trusted workflow; no workflow dispatch."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from wonjae_dispatcher_runner.registered_source_fence import job_adapter
from wonjae_dispatcher_runner.source_writer_fence import FenceError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--phase", required=True)
    args = parser.parse_args()
    adapter = job_adapter(args.registry, args.project, args.checkout)
    if adapter is None:
        print("SOURCE_FENCE_NOT_REGISTERED_LEGACY_ROUTE_RETAINED")
        return 0
    phase = args.phase
    if phase == "acquire":
        # One admission for this exact trusted run; retries cannot mint a new
        # backend task to evade an old grant/PR. Recovery remains exact-run only.
        if adapter.ticket_file.exists():
            raise FenceError("JOB_TICKET_ALREADY_EXISTS")
        adapter.acquire({"route": "direct_worker",
                         "task_id": args.project + "-DIRECT-" + os.environ["GITHUB_RUN_ID"]
                         + "-" + os.environ["GITHUB_RUN_ATTEMPT"],
                         "request_id": secrets.token_hex(16),
                         "expires_at": time.time() + 900, "lease_seconds": 4500})
    elif phase in {"before_source", "before_commit", "before_push", "before_pr", "before_merge"}:
        expected = os.environ["START_HEAD"] if phase in {
            "before_source", "before_commit"
        } else os.environ["DIRECT_HEAD"]
        head = subprocess.run(["git", "-C", str(args.checkout), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        if head != expected:
            raise FenceError("LOCAL_EXPECTED_SHA_CHANGED")
        adapter.before(phase)
    elif phase == "bind_pr":
        raw = adapter.fence.reader.read(
            f"repos/{adapter.fence.binding['repository']}/pulls/{int(os.environ['PR_NUMBER'])}",
        )
        ticket = adapter.ticket()
        if raw.get("state") != "open" or raw["head"]["sha"] != os.environ["DIRECT_HEAD"]:
            raise FenceError("EXACT_PR_READBACK_REQUIRED")
        adapter.bind_pr({"number": raw["number"], "base": raw["base"]["ref"],
                         "head": raw["head"]["sha"], "task_id": ticket["task_id"]})
    elif phase == "ack_integration":
        raw = adapter.fence.reader.read(
            f"repos/{adapter.fence.binding['repository']}/pulls/{int(os.environ['PR_NUMBER'])}",
        )
        if raw.get("merge_commit_sha") != os.environ["INTEGRATED_SHA"]:
            raise FenceError("EXACT_INTEGRATED_READBACK_REQUIRED")
        adapter.fence.acknowledge_integration(adapter.ticket(), {
            "repository": adapter.fence.binding["repository"], "number": raw["number"],
            "base": raw["base"]["ref"], "head": raw["head"]["sha"],
            "merged": raw.get("merged"), "integrated_sha": raw["merge_commit_sha"],
        })
    elif phase == "finish":
        if not adapter.ticket_file.exists():
            return 0
        ticket = adapter.ticket()
        if os.environ.get("SOURCE_FENCE_JOB_STATUS") != "success":
            # No automatic release on cancellation/failure or ambiguous child.
            raise FenceError("NON_SUCCESS_REQUIRES_EXACT_TERMINAL_RECONCILIATION")
        adapter.fence.release(ticket, {"task_id": ticket["task_id"], "epoch": ticket["epoch"],
                                       "child_terminal": True, "publication_reconciled": True})
    else:
        raise FenceError("UNKNOWN_SOURCE_PHASE")
    print("SOURCE_FENCE_BOUNDARY_OK " + phase)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FenceError as error:
        print(json.dumps({"failure": str(error).split(":", 1)[0]}))
        raise SystemExit(2) from None
