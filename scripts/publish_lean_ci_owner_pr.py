"""Future Owner-signed product CI draft PR publisher, default dormant.

Only the separately authenticated SANJINWORKS Owner + trusted main Actions
workflow may run this. The CLI has no merge, CI retry, Provider or deploy mode.
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

from wonjae_dispatcher_runner.lean_ci_owner_stage import SignedWorkflowSelection
from wonjae_dispatcher_runner.lean_ci_pr_recovery import (
    ExactCiRecovery,
    recover_one_registered_ci_pr,
    verify_recovery_claim,
)
from wonjae_dispatcher_runner.lean_ci_pr_publisher import (
    GitHubRest,
    LeanCiPublicationError,
    publish_lean_ci_pr,
    verify_publish_claim,
)


def read_selection() -> SignedWorkflowSelection:
    raw = os.environ.get("LEAN_SELECTION_JSON", "")
    if not raw or len(raw.encode("utf-8")) > 2400:
        raise LeanCiPublicationError("Owner exact claim is missing or too large")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise LeanCiPublicationError("Owner claim must be a JSON mapping")
        return SignedWorkflowSelection(**value)
    except (TypeError, ValueError, KeyError) as exc:
        raise LeanCiPublicationError("Owner exact selection shape invalid") from exc


def read_recovery() -> ExactCiRecovery | None:
    raw = os.environ.get("LEAN_RECOVERY_JSON", "")
    mac = os.environ.get("LEAN_RECOVERY_MAC", "")
    if not raw and not mac:
        return None
    if not raw or not mac or len(raw.encode("utf-8")) > 800:
        raise LeanCiPublicationError("incomplete exact CI recovery authorization")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {
            "pr_number", "pr_head_sha", "failed_run_id", "current_workflow_sha256",
        }:
            raise LeanCiPublicationError("invalid CI recovery claim keys")
        return ExactCiRecovery(**data)
    except (TypeError, ValueError, KeyError) as exc:
        raise LeanCiPublicationError("invalid CI recovery claim") from exc


def check_claim(selection: SignedWorkflowSelection) -> ExactCiRecovery | None:
    if selection.project != os.environ.get("LEAN_INPUT_PROJECT", ""):
        raise LeanCiPublicationError("concurrency project differs from signed identity")
    verify_publish_claim(
        selection,
        os.environ.get("LEAN_CI_PUBLISH_MAC", ""),
        os.environ.get("LEAN_CI_APPROVAL_SECRET", ""),
    )
    recovery = read_recovery()
    if recovery is not None:
        verify_recovery_claim(
            selection, recovery,
            os.environ.get("LEAN_RECOVERY_MAC", ""),
            os.environ.get("LEAN_CI_APPROVAL_SECRET", ""),
        )
    return recovery


def decode_candidate() -> str:
    payload = os.environ.get("LEAN_CI_CANDIDATE_BASE64", "")
    if not payload or len(payload) > 132_000:
        raise LeanCiPublicationError("candidate input missing or too large")
    try:
        decoded = base64.b64decode(payload, validate=True)
        if not decoded or len(decoded) > 28_000:
            raise LeanCiPublicationError("candidate exceeds Owner review limit")
        return decoded.decode("utf-8", "strict")
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise LeanCiPublicationError("invalid candidate bytes") from exc


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check-claim", action="store_true")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--control-checkout", type=Path)
    p.add_argument("--product-checkout", type=Path)
    args = p.parse_args()
    if args.check_claim == args.publish:
        p.error("select exactly one mode")

    try:
        selection = read_selection()
        recovery = check_claim(selection)  # Before checkout or any GitHub resource request.
        if args.check_claim:
            env_file = os.environ.get("GITHUB_ENV", "")
            if env_file:
                with Path(env_file).open("a", encoding="utf-8") as f:
                    for key, value in {
                        "LEAN_PROJECT": selection.project,
                        "LEAN_REPOSITORY": selection.repository,
                        "LEAN_BRANCH": selection.branch,
                        "LEAN_CONTROL_SHA": selection.control_sha,
                        "LEAN_PRODUCT_SHA": selection.product_sha,
                        "LEAN_WORKFLOW_PATH": selection.workflow_path,
                    }.items():
                        f.write(key + "=" + value + "\n")
            print("LEAN_CI_PUBLISH_SCOPE_VERIFIED_NOT_EXECUTED")
            return 0
        if not args.control_checkout or not args.product_checkout:
            raise LeanCiPublicationError("exact source checkouts are missing")
        proposal = decode_candidate()
        token = os.environ.get("OWNER_GITHUB_TOKEN", "")
        api = GitHubRest(selection.repository, token)
        shared = dict(
            control_checkout=args.control_checkout,
            product_checkout=args.product_checkout,
            selection=selection,
            publish_mac=os.environ.get("LEAN_CI_PUBLISH_MAC", ""),
            approval_secret=os.environ.get("LEAN_CI_APPROVAL_SECRET", ""),
            candidate=proposal,
            api=api,
        )
        if recovery is not None:
            receipt = recover_one_registered_ci_pr(
                **shared, recovery=recovery,
                recovery_mac=os.environ.get("LEAN_RECOVERY_MAC", ""),
            )
        else:
            receipt = publish_lean_ci_pr(**shared)
        print(json.dumps(asdict(receipt), ensure_ascii=True, sort_keys=True))
        return 0
    except (LeanCiPublicationError, OSError):
        # No source, token, candidate, Github response body or stacktrace.
        print(
            "LEAN_CI_PR_PUBLISH_BLOCKED: inspect exact scope and existing product PR",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
