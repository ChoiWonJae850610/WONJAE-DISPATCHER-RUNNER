"""Offline inspection of bounded trusted Direct Worker timeout log evidence.

No Github API, authentication, Codex client, runner action, credential rotation,
model request or file modification. The supplied log's content is NEVER echoed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from wonjae_dispatcher_runner.codex_safe_diagnostic import (
    MAX_LOG_BYTES,
    classify_historic_log,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline fixed-marker timeout classifier")
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.log.is_symlink() or not args.log.is_file():
            raise ValueError("not a regular log")
        if args.log.stat().st_size > MAX_LOG_BYTES:
            raise ValueError("too large")
        content = args.log.read_text(encoding="utf-8", errors="replace")
        result = classify_historic_log(content)
    except (OSError, ValueError):
        print("SAFE_CODEX_LOG_ANALYSIS status=UNREADABLE_OR_UNSAFE_INPUT")
        return 2

    print(
        f"SAFE_CODEX_LOG_ANALYSIS status={result.status} "
        f"timeout_attempts={result.attempts} phase={result.phase}"
    )
    return 0 if result.status in {
        "LEGACY_TIMEOUT_PHASE_UNKNOWN", "TIMEOUT_PHASE_OBSERVED",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
