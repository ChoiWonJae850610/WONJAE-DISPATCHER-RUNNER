from __future__ import annotations

import os
from pathlib import Path

from wonjae_dispatcher_runner.auth_store import restore_auth_json
from wonjae_dispatcher_runner.product_patch import generate_and_apply_product_patch


def main() -> int:
    repo_path = Path(os.environ["PRODUCT_CHECKOUT"]).resolve()
    work_order_path = Path(os.environ["WORK_ORDER_FILE"]).resolve()
    codex_home = Path(os.environ["CODEX_HOME"]).resolve()
    control_sha = os.environ["CONTROL_SHA"]

    restore_auth_json(codex_home, os.environ["CODEX_AUTH_JSON"])
    work_order, paths = generate_and_apply_product_patch(
        repo_path,
        work_order_path,
        control_sha,
        codex_home,
    )
    print(f"PRODUCT_TASK_ID={work_order.task_id}")
    print(f"PRODUCT_CHANGED_PATH_COUNT={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
