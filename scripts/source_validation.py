"""Read Actions REST status only; no annotations, repair, dispatch or merge authority."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wonjae_dispatcher_runner.source_validation import (  # noqa: E402
    SourceValidationError,
    wait_exact_validation,
)


def read_run(run_id, *, timeout_seconds):
    output = subprocess.run(
        ["gh", "api", "--method", "GET",
         f"repos/{os.environ['PRODUCT_REPOSITORY']}/actions/runs/{run_id}"],
        check=True, capture_output=True, text=True, timeout=timeout_seconds,
    ).stdout
    return json.loads(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--event", choices=("pull_request", "push"), required=True)
    args = parser.parse_args(argv)
    try:
        conclusion = wait_exact_validation(
            read_run, repository=os.environ["PRODUCT_REPOSITORY"],
            workflow_path=f".github/workflows/{os.environ['VALIDATION_WORKFLOW_FILE']}",
            run_id=args.run_id, head_sha=args.head_sha, event=args.event,
        )
    except SourceValidationError as exc:
        evidence = str(exc)
        print(evidence, file=sys.stderr)
        if os.environ.get("GITHUB_ENV"):
            with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as env:
                env.write(f"SOURCE_TERMINAL_RESULT={exc.terminal_result}\n")
                env.write(f"SOURCE_VALIDATION_EVIDENCE={evidence}\n")
        return 1
    print(f"{conclusion} {args.head_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
