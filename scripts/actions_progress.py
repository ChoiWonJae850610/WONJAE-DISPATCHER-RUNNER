from __future__ import annotations

import argparse
import os
import re
from datetime import UTC, datetime
from pathlib import Path

PHASES = (
    "WAKE",
    "CONTROL",
    "PRODUCT",
    "CODEX",
    "WRITE",
    "STARTED",
    "VALIDATE",
    "INTEGRATE",
    "RESULT",
)
STATES = ("RUNNING", "PASS", "SKIP", "GATE", "FAIL")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=PHASES)
    parser.add_argument("state", choices=STATES)
    parser.add_argument("--sha")
    parser.add_argument("--pr", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sha is not None and SHA_RE.fullmatch(args.sha) is None:
        raise SystemExit("--sha must be a lowercase 40-character Git SHA")
    if args.pr is not None and args.pr <= 0:
        raise SystemExit("--pr must be positive")

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    details: list[str] = []
    if args.sha:
        details.append(f"SHA `{args.sha}`")
    if args.pr:
        details.append(f"PR #{args.pr}")
    detail_text = " · ".join(details) if details else "—"

    line = f"[{args.phase}] {args.state}"
    if details:
        line += " · " + " · ".join(details)
    print(f"::notice title=Dispatcher Progress::{line}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        path = Path(summary_path)
        if not path.exists() or path.stat().st_size == 0:
            path.write_text(
                "# Dispatcher progress\n\n"
                "| Time (UTC) | Phase | State | Evidence |\n"
                "| --- | --- | --- | --- |\n",
                encoding="utf-8",
            )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"| {timestamp} | {args.phase} | **{args.state}** | {detail_text} |\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
