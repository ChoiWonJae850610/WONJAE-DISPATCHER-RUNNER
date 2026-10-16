"""Owner-signed, offline-only CI workflow staging; never commit, push, or dispatch.

A future explicitly approved trusted Runner action must supply isolated exact
checkouts, a reviewed candidate and an independent Owner HMAC secret. This CLI
does not create an executable product source-development path by itself.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from wonjae_dispatcher_runner.lean_ci_owner_stage import (
    LeanCiOwnerStageError,
    SignedWorkflowSelection,
    stage_workflow,
    verify_workflow_selection,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-checkout", required=True, type=Path)
    parser.add_argument("--product-checkout", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--workflow-path", required=True)
    parser.add_argument("--control-sha", required=True)
    parser.add_argument("--product-sha", required=True)
    parser.add_argument("--previous-sha256", required=True)
    parser.add_argument("--proposed-sha256", required=True)
    parser.add_argument("--expires-ms", required=True, type=int)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()

    try:
        selection = SignedWorkflowSelection(
            project=args.project,
            repository=args.repository,
            branch=args.branch,
            workflow_path=args.workflow_path,
            control_sha=args.control_sha,
            product_sha=args.product_sha,
            previous_sha256=args.previous_sha256,
            proposed_sha256=args.proposed_sha256,
            expires_ms=args.expires_ms,
            nonce=args.nonce,
        )
        secret = os.environ.get("LEAN_CI_APPROVAL_SECRET", "")
        mac = os.environ.get("LEAN_CI_APPROVAL_MAC", "")
        # Check signature and age before decoding untrusted candidate bytes.
        verify_workflow_selection(selection, mac, secret)
        encoded = os.environ.get("LEAN_CI_CANDIDATE_BASE64", "")
        if not encoded or len(encoded) > 132_000:
            raise LeanCiOwnerStageError("candidate missing or exceeds bounded input")
        try:
            payload = base64.b64decode(encoded, validate=True)
            if not payload or len(payload) > 96_000:
                raise LeanCiOwnerStageError("candidate content outside allowed size")
            candidate = payload.decode("utf-8", errors="strict")
        except (binascii.Error, UnicodeError, ValueError) as exc:
            raise LeanCiOwnerStageError("candidate is not strict UTF-8/base64") from exc
        result = stage_workflow(
            control_checkout=args.control_checkout,
            product_checkout=args.product_checkout,
            selection=selection,
            approval_mac=mac,
            approval_secret=secret,
            candidate=candidate,
        )
    except (LeanCiOwnerStageError, OSError, ValueError):
        print("OWNER_LEAN_CI_STAGE_REJECTED: exact authorization or source guard failed", file=sys.stderr)
        return 2
    # Do not echo candidate, raw secrets or a false merged/validated status.
    print(json.dumps(asdict(result), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
