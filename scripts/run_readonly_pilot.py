from __future__ import annotations

import os
from pathlib import Path

from wonjae_dispatcher_runner.auth_store import restore_auth_json
from wonjae_dispatcher_runner.codex_runner import run_readonly_head_probe
from wonjae_dispatcher_runner.guards import ReadonlyPilotRequest


def main() -> int:
    request = ReadonlyPilotRequest.validate(
        os.environ["CONTROL_REPOSITORY"],
        os.environ["EXPECTED_CONTROL_SHA"],
    )
    repo_path = Path(os.environ["CONTROL_CHECKOUT"]).resolve()
    codex_home = Path(os.environ["CODEX_HOME"]).resolve()

    restore_auth_json(codex_home, os.environ["CODEX_AUTH_JSON"])
    reported = run_readonly_head_probe(repo_path, request.expected_sha, codex_home)
    print(f"READONLY_PILOT_EXACT_SHA={reported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
