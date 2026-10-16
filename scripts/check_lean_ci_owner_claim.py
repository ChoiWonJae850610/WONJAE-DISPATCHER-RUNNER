"""Check signed SANJINWORKS Owner input before any private product checkout."""

from __future__ import annotations

import os
import sys

from wonjae_dispatcher_runner.lean_ci_owner_stage import (
    LeanCiOwnerStageError,
    SignedWorkflowSelection,
    verify_workflow_selection,
)


def selection_from_environment() -> SignedWorkflowSelection:
    return SignedWorkflowSelection(
        project=os.environ.get("LEAN_PROJECT", ""),
        repository=os.environ.get("LEAN_REPOSITORY", ""),
        branch=os.environ.get("LEAN_BRANCH", ""),
        workflow_path=os.environ.get("LEAN_WORKFLOW_PATH", ""),
        control_sha=os.environ.get("LEAN_CONTROL_SHA", ""),
        product_sha=os.environ.get("LEAN_PRODUCT_SHA", ""),
        previous_sha256=os.environ.get("LEAN_PREVIOUS_SHA256", ""),
        proposed_sha256=os.environ.get("LEAN_PROPOSED_SHA256", ""),
        expires_ms=int(os.environ.get("LEAN_EXPIRES_MS", "0")),
        nonce=os.environ.get("LEAN_NONCE", ""),
    )


def main() -> int:
    try:
        selection = selection_from_environment()
        verify_workflow_selection(
            selection,
            os.environ.get("LEAN_CI_APPROVAL_MAC", ""),
            os.environ.get("LEAN_CI_APPROVAL_SECRET", ""),
        )
    except (LeanCiOwnerStageError, ValueError, TypeError):
        print("OWNER_LEAN_CI_APPROVAL_REJECTED", file=sys.stderr)
        return 2
    print("OWNER_LEAN_CI_APPROVAL_VALID_FOR_STAGING_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
