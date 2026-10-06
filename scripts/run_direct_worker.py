from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from wonjae_dispatcher_runner.auth_store import restore_auth_json
from wonjae_dispatcher_runner.direct_worker import (
    DirectWorkerTurnTimeout,
    load_direct_worker_route,
    run_direct_worker,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--product-checkout", required=True)
    parser.add_argument("--command", choices=("next", "retry", "resume"), required=True)
    parser.add_argument("--failure-log")
    parser.add_argument("--result-file", required=True)
    args = parser.parse_args()

    codex_home = Path(os.environ["CODEX_HOME"]).resolve()
    restore_auth_json(codex_home, os.environ["CODEX_AUTH_JSON"])
    route = load_direct_worker_route(Path(args.registry), args.project)
    failure = ""
    if args.failure_log:
        path = Path(args.failure_log)
        if path.is_file():
            failure = path.read_text(encoding="utf-8", errors="replace")

    try:
        result = run_direct_worker(
            Path(args.product_checkout).resolve(), route, args.command, codex_home, failure,
        )
    except DirectWorkerTurnTimeout:
        print("INITIAL_CODEX_TIMEOUT_480_SECONDS", flush=True)
        return 75
    Path(args.result_file).write_text(
        json.dumps(
            {
                "status": result.status,
                "summary": result.summary,
                "manual_action": result.manual_action,
                "changed_paths": list(result.changed_paths),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"DIRECT_WORKER_STATUS={result.status}")
    print(f"DIRECT_WORKER_CHANGED_PATH_COUNT={len(result.changed_paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
