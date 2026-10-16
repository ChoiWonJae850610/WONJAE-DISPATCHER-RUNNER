"""Verify exact signed claim before private reads, then expose trusted job env."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from wonjae_dispatcher_runner.lean_ci_owner_stage import (
    LeanCiOwnerStageError,
    SignedWorkflowSelection,
    verify_workflow_selection,
)


def selection_from_environment() -> SignedWorkflowSelection:
    raw = os.environ.get("LEAN_SELECTION_JSON", "")
    if not raw or len(raw.encode("utf-8")) > 2400:
        raise LeanCiOwnerStageError("missing or oversized exact selection")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise LeanCiOwnerStageError("selection must be a JSON object")
        return SignedWorkflowSelection(**body)
    except (ValueError, TypeError, KeyError) as exc:
        raise LeanCiOwnerStageError("invalid exact selection shape") from exc


def main() -> int:
    try:
        selection = selection_from_environment()
        verify_workflow_selection(
            selection,
            os.environ.get("LEAN_CI_APPROVAL_MAC", ""),
            os.environ.get("LEAN_CI_APPROVAL_SECRET", ""),
        )
        env_file = os.environ.get("GITHUB_ENV")
        if env_file:
            values = {
                "LEAN_PROJECT": selection.project,
                "LEAN_REPOSITORY": selection.repository,
                "LEAN_BRANCH": selection.branch,
                "LEAN_WORKFLOW_PATH": selection.workflow_path,
                "LEAN_CONTROL_SHA": selection.control_sha,
                "LEAN_PRODUCT_SHA": selection.product_sha,
                "LEAN_PREVIOUS_SHA256": selection.previous_sha256,
                "LEAN_PROPOSED_SHA256": selection.proposed_sha256,
                "LEAN_EXPIRES_MS": str(selection.expires_ms),
                "LEAN_NONCE": selection.nonce,
            }
            with Path(env_file).open("a", encoding="utf-8") as target:
                for key, value in values.items():
                    target.write(f"{key}={value}\n")
    except (LeanCiOwnerStageError, ValueError, TypeError, OSError):
        print("OWNER_LEAN_CI_APPROVAL_REJECTED", file=sys.stderr)
        return 2
    print("OWNER_LEAN_CI_APPROVAL_VALID_FOR_STAGING_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
