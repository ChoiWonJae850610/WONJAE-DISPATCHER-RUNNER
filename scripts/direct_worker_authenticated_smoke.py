"""Trusted-main authenticated synthetic turn; no product token or publication.

Exercises the actual Direct Worker source function in a fresh synthetic checkout.
All secrets are consumed by trusted bootstrap, never by its source app-server/tools.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from wonjae_dispatcher_runner.auth_store import restore_auth_json
from wonjae_dispatcher_runner.direct_worker import (
    DirectWorkerRoute,
    changed_paths,
    git_head,
    git_metadata_snapshot,
    run_direct_worker,
)
from wonjae_dispatcher_runner.home_isolation import require_linux_host


def main() -> int:
    require_linux_host()
    parent = Path(os.environ["DW_SMOKE_ROOT"]).resolve(strict=True)
    auth = parent / "auth"
    restore_auth_json(auth, os.environ["CODEX_AUTH_JSON"])
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=parent) as temporary:
        product = Path(temporary)
        subprocess.run(["git", "init", "-q", "-b", "direct/synthetic", str(product)],
                       check=True)
        for name in ("AGENTS.md", "PROJECT_RULES.md", "README.md"):
            (product / name).write_text(
                "Synthetic Direct Worker acceptance only. Edit source.txt only. "
                "No product, provider, Git publication or external authority.\n"
            )
        (product / "source.txt").write_text("before\n")
        subprocess.run(["git", "-C", str(product), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(product), "-c", "user.name=Synthetic",
            "-c", "user.email=synthetic@example.invalid", "-c", "core.hooksPath=/dev/null",
            "commit", "-q", "-m", "synthetic fixture",
        ], check=True)
        original = git_head(product)
        metadata = git_metadata_snapshot(product)
        # Synthetic route cannot publish and is never loaded into the private registry.
        route = DirectWorkerRoute(
            project="ESC", repository="synthetic/no-publication", branch="direct/synthetic",
            startup_entry="AGENTS.md", project_rules="PROJECT_RULES.md",
            canonical_docs=("README.md",), validation_workflow_path="synthetic",
            validation_workflow_name="synthetic", runner_repository="synthetic",
            runner_workflow="synthetic", state_path=None, handoff_path=None, handoff=None,
        )
        os.environ["DIRECT_WORKER_HOME_ISOLATION"] = "1"
        result = run_direct_worker(
            product, route, "retry", auth,
            "Synthetic isolated acceptance fixture, not a registered product task. "
            "The only required correction is source.txt: replace before with after, "
            "retaining one trailing newline. Use a shell command to check GH_TOKEN, "
            "PRODUCT_TOKEN, RUNNER_TOKEN and CODEX_AUTH_JSON are unset. "
            "Do not commit or publish. Return CHANGED only after the source correction.",
        )
        if (result.status != "CHANGED" or changed_paths(product) != ("source.txt",)
                or (product / "source.txt").read_text() != "after\n"
                or git_head(product) != original or git_metadata_snapshot(product) != metadata):
            raise RuntimeError("authenticated synthetic source boundary failed")
        print("authenticated workspace-write source / unchanged Git: PASS")
        # Only this trusted parent performs a Git write, after source boundary readback.
        subprocess.run(["git", "-C", str(product), "add", "source.txt"], check=True)
        subprocess.run([
            "git", "-C", str(product), "-c", "user.name=Synthetic",
            "-c", "user.email=synthetic@example.invalid", "-c", "core.hooksPath=/dev/null",
            "commit", "-q", "-m", "trusted synthetic publication boundary",
        ], check=True)
        if git_head(product) == original:
            raise RuntimeError("trusted synthetic Git boundary failed")
        print("trusted-parent local commit: PASS")
    print("product push/PR/provider/runtime/EAS/device/physical: NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
