#!/usr/bin/env python3
"""Exact CLASSMO P2 Development LIVE adapter handoff, Owner only."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from classmo_editorial_027_p2_live_guard import verify_source

ADAPTER = "scripts/classmo_editorial_p2_live_owner_adapter.py"
REF = "hfclkbqzxiqhcckwknih"
SHA = re.compile(r"[a-f0-9]{40}\Z")


class Blocked(Exception):
    """Only safe, fixed error codes leave the trusted Runner."""


def require(value: bool, code: str) -> None:
    if not value:
        raise Blocked(code)


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True,
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise Blocked("CLASSMO_P2_ADAPTER_GIT_UNVERIFIED") from None


def admit(repo: Path, sha: str, ref: str, blob: str) -> Path:
    require(ref == REF and SHA.fullmatch(sha) is not None,
            "CLASSMO_P2_LIVE_TARGET_UNREGISTERED")
    require(SHA.fullmatch(blob) is not None, "CLASSMO_P2_ADAPTER_BLOB_UNPINNED")
    verify_source(repo, sha, ref)
    path = repo / ADAPTER
    require(path.is_file() and not path.is_symlink()
            and path.resolve().is_relative_to(repo.resolve()),
            "CLASSMO_P2_LIVE_ADAPTER_MISSING")
    require(git(repo, "rev-parse", "HEAD") == sha
            and git(repo, "rev-parse", f"{sha}:{ADAPTER}") == blob
            and git(repo, "hash-object", ADAPTER) == blob,
            "CLASSMO_P2_LIVE_ADAPTER_CHANGED")
    return path


def verify_evidence(data: object, sha: str) -> None:
    require(isinstance(data, dict) and data.get("schema_version") == 1
            and data.get("source_sha") == sha and data.get("project_ref") == REF
            and data.get("status") == "INDEPENDENTLY_VERIFIED",
            "CLASSMO_P2_LIVE_RECEIPT_UNVERIFIED")
    phases = data.get("phases")
    require(isinstance(phases, dict), "CLASSMO_P2_LIVE_PHASES_MISSING")
    for name in ("edge_wasm", "license", "images", "auth_rls",
                 "postgres_races", "cleanup"):
        require(isinstance(phases.get(name), dict)
                and phases[name].get("status") == "PASS"
                and phases[name].get("real_provider_readback") is True,
                "CLASSMO_P2_LIVE_PHASE_NOT_VERIFIED")
    require(phases["images"].get("formats") == ["png", "jpeg-exif", "webp"]
            and phases["images"].get("independent_private_download") is True
            and phases["auth_rls"].get("role_action_cases") == 108,
            "CLASSMO_P2_LIVE_BYTES_OR_RLS_UNVERIFIED")
    require(phases["postgres_races"].get("independent_pids") is True
            and phases["postgres_races"].get("lock_barriers") == 2
            and phases["cleanup"].get("independent_zero_residue") is True
            and phases["cleanup"].get("unknown_keys") == [],
            "CLASSMO_P2_LIVE_RACE_OR_CLEANUP_UNVERIFIED")


def execute(repo: Path, sha: str, ref: str, blob: str,
            evidence: Path, approved: bool) -> str:
    adapter = admit(repo, sha, ref, blob)
    if not approved:
        return "CLASSMO_P2_ADAPTER_SOURCE_CHECK_ONLY_NOT_LIVE"
    require(os.getenv("GITHUB_ACTIONS") == "true"
            and os.getenv("WONJAE_OWNER_LIVE_APPROVED") == "yes",
            "CLASSMO_P2_OWNER_NOT_AUTHENTICATED")
    require(bool(os.getenv("CLASSMO_SUPABASE_ACCESS_TOKEN"))
            and bool(os.getenv("CLASSMO_G1V_DEV_DB_PASSWORD")),
            "CLASSMO_P2_DEV_PROVIDER_CONNECTION_NOT_READY")
    require(not evidence.exists(), "CLASSMO_P2_RECEIPT_ALREADY_EXISTS")
    try:
        result = subprocess.run(
            [sys.executable, str(adapter), "--source-sha", sha,
             "--project-ref", ref, "--evidence", str(evidence)],
            cwd=repo, check=False, timeout=900,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("CLASSMO_P2_LIVE_ADAPTER_EXECUTION_FAILED") from None
    require(result.returncode == 0 and evidence.is_file()
            and not evidence.is_symlink() and evidence.stat().st_size <= 262144,
            "CLASSMO_P2_LIVE_RECEIPT_MISSING")
    try:
        value = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Blocked("CLASSMO_P2_LIVE_RECEIPT_INVALID") from None
    verify_evidence(value, sha)
    return "CLASSMO_P2_LIVE_EVIDENCE_PRESENT_DEVICE_QA_NOT_RUN"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--project-ref", required=True)
    p.add_argument("--adapter-blob", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    try:
        result = execute(Path(args.repo).resolve(), args.source_sha,
                         args.project_ref, args.adapter_blob,
                         Path(args.evidence), args.execute)
    except Exception as exc:
        code = str(exc) if isinstance(exc, Blocked) else "CLASSMO_P2_LIVE_NOT_VERIFIED"
        print("CLASSMO_P2_LIVE_BLOCKED code=" + code, flush=True)
        return 1
    print(result, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
