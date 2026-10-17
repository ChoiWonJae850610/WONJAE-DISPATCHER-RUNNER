#!/usr/bin/env python3
"""Exact WAFL G1-V1/G1-V2 source admission. No live acceptance or Provider calls.

This *preparation* guard must never be interpreted as an authenticated Apple
runtime/RLS/RPC, Neon invariant/data-equivalence or G1_ACCEPT verifier.
The Owner runtime route remains disabled until independent actual executors,
two lawful native Apple sessions and named 50-constraint evidence exist.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

PROJECT_REF = "aqqarddfmdfyzeonfgwl"
SOURCE_BASELINE = "b22bc730e409c2468ba92aad2cb962e8035c916f"
G1V1_PATH = "supabase/tests/wafl_g1v_plan.json"
G1V1_BLOB = "7183ab1e76061b7f4896bd52a04d16fb039bff47"
G1V2_PATH = "supabase/stage4/identity-label-migration-manifest.json"
G1V2_BLOB = "febe58943d1b98f042053091798aa88fc3d8b345"
SHA = re.compile(r"[0-9a-f]{40}\Z")


class GuardBlocked(Exception):
    """Never include live user, token, subject or DB values in the error."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GuardBlocked(code)


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise GuardBlocked("WAFL_G1_SOURCE_GIT_UNVERIFIED") from None


def verify_source(repo: Path, source_sha: str, project_ref: str) -> dict:
    require(project_ref == PROJECT_REF, "WAFL_G1_TARGET_NOT_REGISTERED")
    require(bool(SHA.fullmatch(source_sha)), "WAFL_G1_SOURCE_SHA_INVALID")
    require(git(repo, "rev-parse", "HEAD") == source_sha,
            "WAFL_G1_SOURCE_HEAD_CHANGED")
    require(
        git(repo, "merge-base", "--is-ancestor", SOURCE_BASELINE, source_sha) == "",
        "WAFL_G1_BASELINE_NOT_ACCEPTED",
    )
    for path, expected in ((G1V1_PATH, G1V1_BLOB), (G1V2_PATH, G1V2_BLOB)):
        require(git(repo, "rev-parse", f"{source_sha}:{path}") == expected,
                "WAFL_G1_PINNED_SOURCE_CHANGED")
        full = repo / path
        require(full.is_file() and not full.is_symlink()
                and full.resolve().is_relative_to(repo.resolve()),
                "WAFL_G1_SOURCE_FILE_UNSAFE")
    try:
        plan = json.loads((repo / G1V1_PATH).read_text(encoding="utf-8"))
        manifest = json.loads((repo / G1V2_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GuardBlocked("WAFL_G1_SOURCE_CONTRACT_INVALID") from None
    limits = manifest.get("notValidConstraints") if isinstance(manifest, dict) else None
    require(isinstance(plan, dict)
            and plan.get("schema_version") == 1
            and plan.get("task") == "WAFL-G1V-RPC-RLS-VERIFY-002"
            and plan.get("remote_execution_authorized") is False
            and plan.get("provider_contract", {}).get("project_ref") == PROJECT_REF,
            "WAFL_G1V1_PLAN_IDENTITY_MISMATCH")
    require(isinstance(limits, dict) and limits.get("total") == 50
            and limits.get("foreignKeys") == 46 and limits.get("checks") == 4
            and limits.get("closed") == 0
            and limits.get("namedInventorySupplied") is False,
            "WAFL_G1V2_50_INVARIANT_BASELINE_CHANGED")
    return {
        "result": "WAFL_G1V1_V2_PREPARATION_ONLY_NOT_RUNTIME_ACCEPTANCE",
        "project": "WAFL",
        "source_sha": source_sha,
        "g1v1_004_005_006_apply": "PREVIOUSLY_VERIFIED_NOT_REPLAYED",
        "g1v1_readonly_run": "39014181600_PREVIOUSLY_SUCCESS_NOT_REPLAYED",
        "g1v1_native_apple_two_subjects": "NOT_VERIFIED",
        "g1v1_authenticated_rls_rpc_two_sessions": "NOT_RUN",
        "g1v1_identity_map": "NOT_ACCEPTED",
        "g1v2_neon_constraints": "50_OPEN_46_FK_4_CHECK",
        "g1v2_domain_data_equivalence": "NOT_VERIFIED",
        "g1_accept": "NOT_ACCEPTED",
        "provider_reads": 0,
        "provider_writes": 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--source-sha", required=True)
    ap.add_argument("--project-ref", required=True)
    args = ap.parse_args()
    try:
        result = verify_source(Path(args.repo).resolve(), args.source_sha,
                               args.project_ref)
    except GuardBlocked as err:
        print("WAFL_G1_PREPARATION_BLOCKED code=" + str(err), flush=True)
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
