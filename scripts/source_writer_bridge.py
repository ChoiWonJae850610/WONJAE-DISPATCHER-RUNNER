"""Trusted bridge CLI. JSON through stdin; no daemon and no secret output."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from wonjae_dispatcher_runner.source_writer_fence import (
    FenceError,
    GitFixtureReader,
    SharedFence,
    process_terminal,
)


def invoke(request: dict, reader=None) -> dict:
    if request.get("registered"):
        from wonjae_dispatcher_runner.registered_source_fence import watch_adapter
        fence, local_head = watch_adapter(
            Path(request["registry"]), request["project"], Path(request["source_root"]),
            request["configuration"], request.get("ticket"),
        )
        if request["action"] in {"acquire", "begin_child"} and local_head != (
            fence.binding["remote_sha"]
        ):
            raise FenceError("WATCH_LOCAL_SHA_CHANGED_BEFORE_SOURCE")
        binding = fence.binding
    else:
        binding = request["binding"]
    if reader is None and not request.get("registered"):
        # Fixture transport cannot assert live provenance. The backend marker is
        # checked independently and cannot be upgraded with a caller boolean.
        reader = GitFixtureReader(binding, Path(request["git_directory"]),
                                  Path(request["facts_file"]))
    if not request.get("registered"):
        fence = SharedFence(Path(request["root"]), binding, reader)
    if reader is not None and reader.__class__ is GitFixtureReader and fence.scope != "fixture":
        raise FenceError("FIXTURE_READER_CANNOT_ATTEST_LIVE")
    action = request["action"]
    if action == "acquire":
        ticket = fence.acquire(request["request"])
        return {"accepted": True, "ticket": ticket, "scope": fence.scope}
    ticket = request["ticket"]
    if action == "verify":
        return fence.verify(ticket, request["phase"])
    if action == "bind_pr":
        fence.bind_pr(ticket, request["pr"])
    elif action == "begin_child":
        fence.begin_child(ticket, request["supervisor_pid"])
    elif action == "attach_child":
        fence.attach_child(ticket, request["pid"])
    elif action == "finish_child":
        fence.finish_child(ticket)
    elif action == "release":
        fence.release(ticket, request["receipt"])
    elif action == "recover":
        fence.recover(ticket, request["receipt"], process_terminal)
    else:
        raise FenceError("UNKNOWN_BRIDGE_ACTION")
    return {"accepted": True, "scope": fence.scope}


def main() -> int:
    try:
        answer = invoke(json.load(sys.stdin))
    except (FenceError, KeyError, ValueError, OSError) as exc:
        # Fixed code only; never echo request/binding/credential or child output.
        print(json.dumps({"accepted": False, "error": str(exc).split(":", 1)[0]}))
        return 2
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
