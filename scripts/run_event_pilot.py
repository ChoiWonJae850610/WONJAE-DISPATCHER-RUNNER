from __future__ import annotations

import os
from pathlib import Path

from wonjae_dispatcher_runner.auth_store import restore_auth_json
from wonjae_dispatcher_runner.event_pilot import run_event_pilot
from wonjae_dispatcher_runner.guards import require_sha


def main() -> int:
    repo_path = Path(os.environ["CONTROL_CHECKOUT"]).resolve()
    codex_home = Path(os.environ["CODEX_HOME"]).resolve()
    expected_sha = require_sha(os.environ["EXPECTED_CONTROL_SHA"])

    restore_auth_json(codex_home, os.environ["CODEX_AUTH_JSON"])
    target = run_event_pilot(repo_path, expected_sha, codex_home)
    print(f"EVENT_PILOT_PATH={target.relative_to(repo_path).as_posix()}")
    print(f"EVENT_PILOT_BASE_SHA={expected_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
