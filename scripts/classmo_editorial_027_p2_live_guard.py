#!/usr/bin/env python3
"""CLASSMO 027-P2 pinned source admission only; never a live Provider verifier."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

SOURCE_BASELINE = "e635a359f9393efa4e5a8fe54ab9ec7aa9cb389c"
PROJECT_REF = "hfclkbqzxiqhcckwknih"
PLAN_PATH = "supabase/tests/editorial_media_p2_plan.json"
PLAN_BLOB = "2aebbaf9dfa2699c60d14e3e614927d6653103c1"
SHA = re.compile(r"[0-9a-f]{40}\Z")


class GuardBlocked(Exception):
    """Safe, nonsecret error code only."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GuardBlocked(code)


def git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise GuardBlocked("CLASSMO_P2_SOURCE_GIT_UNVERIFIED") from None


def verify_source(repo: Path, source_sha: str, project_ref: str) -> dict:
    require(project_ref == PROJECT_REF, "CLASSMO_P2_PROJECT_REF_MISMATCH")
    require(bool(SHA.fullmatch(source_sha)),
            "CLASSMO_P2_SOURCE_SHA_INVALID")
    require(git(repo, "rev-parse", "HEAD") == source_sha,
            "CLASSMO_P2_SOURCE_HEAD_CHANGED")
    git(repo, "merge-base", "--is-ancestor", SOURCE_BASELINE, source_sha)
    require(git(repo, "rev-parse", f"{source_sha}:{PLAN_PATH}") == PLAN_BLOB,
            "CLASSMO_P2_PLAN_BLOB_CHANGED")
    plan_path = repo / PLAN_PATH
    require(plan_path.is_file() and not plan_path.is_symlink(),
            "CLASSMO_P2_PLAN_FILE_UNSAFE")
    require(git(repo, "hash-object", PLAN_PATH) == PLAN_BLOB,
            "CLASSMO_P2_PLAN_WORKTREE_CHANGED")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GuardBlocked("CLASSMO_P2_PLAN_INVALID") from None
    require(
        isinstance(plan, dict)
        and plan.get("schema_version") == 1
        and plan.get("project_ref") == PROJECT_REF
        and plan.get("environment") == "development"
        and plan.get("bucket", {}).get("id") == "classmo-editorial"
        and plan.get("bucket", {}).get("public") is False,
        "CLASSMO_P2_PLAN_TARGET_MISMATCH",
    )
    execution = plan.get("execution_contract", {})
    normalizer = plan.get("normalization_source", {})
    require(
        isinstance(execution, dict)
        and execution.get("provider_status") == "NOT_REGISTERED / NOT_RUN"
        and isinstance(normalizer, dict)
        and normalizer.get("live_status") == "NOT_RUN",
        "CLASSMO_P2_PLAN_LIVE_STATUS_UNREVIEWED",
    )
    return {
        "result": "CLASSMO_P2_OFFLINE_ADMISSION_NOT_RUNTIME_ACCEPTANCE",
        "source_sha": source_sha,
        "p1_017_schema_storage": "PREVIOUSLY_VERIFIED_NOT_REPLAYED",
        "p2_readonly_provider": "39017423702_PREVIOUS_SUCCESS_NOT_REPLAYED",
        "edge_deployment_and_wasm_bundle": "NOT_RUN",
        "server_license_approval_and_receipt": "NOT_VERIFIED",
        "real_png_jpeg_webp_and_exif": "NOT_RUN",
        "authenticated_roles_and_tenant_rls": "NOT_RUN",
        "independent_postgres_race": "NOT_RUN",
        "storage_db_auth_zero_residue": "NOT_RUN",
        "p2_live_acceptance": "NOT_ACCEPTED",
        "provider_reads": 0,
        "provider_writes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--project-ref", required=True)
    args = parser.parse_args()
    try:
        receipt = verify_source(
            Path(args.repo).resolve(), args.source_sha, args.project_ref
        )
    except GuardBlocked as exc:
        print("CLASSMO_P2_PREPARATION_BLOCKED code=" + str(exc), flush=True)
        return 1
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
