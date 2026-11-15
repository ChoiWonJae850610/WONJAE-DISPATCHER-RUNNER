"""One-shot trusted work/publication boundary over the existing shared fence."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wonjae_dispatcher_runner.product_git_fence import MutationFence  # noqa: E402
from wonjae_dispatcher_runner.source_writer_fence import FenceError  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("before", "pr", "merge", "finish"))
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--phase", default="before_pr")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--head")
    parser.add_argument("--integrated")
    args = parser.parse_args()
    approved = [(args.pr, args.head)] if args.pr and args.head else []
    scope = MutationFence(args.registry, args.project, args.actor, args.role,
                          args.base, approved, persist=True)
    try:
        scope._initialize()
        if args.action == "before":
            scope.point(args.phase)
        elif args.action == "pr":
            scope.observed_pr(args.pr, args.head)
        elif args.action == "merge":
            scope.observed_merge(args.pr, args.integrated)
        else:
            if os.environ.get("PRODUCT_WRITER_JOB_STATUS", "success") != "success":
                raise FenceError("UNCERTAIN_TERMINAL_NO_RELEASE")
            scope.finish()
    except BaseException:
        scope.abort()
        raise
    print(json.dumps({"boundary": args.action,
                      "state": "VERIFIED" if scope.fence else "LEGACY_NOT_REGISTERED"}))


if __name__ == "__main__":
    try:
        main()
    except (FenceError, OSError, KeyError, ValueError):
        print("PRODUCT_WRITER_FENCE_REFUSED", file=sys.stderr)
        raise SystemExit(2) from None
