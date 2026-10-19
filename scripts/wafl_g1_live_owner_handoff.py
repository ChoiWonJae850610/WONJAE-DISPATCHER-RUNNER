#!/usr/bin/env python3
"""Exact WAFL Apple/Neon Development LIVE Owner adapter handoff."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from wafl_g1v1v2_preparation_guard import verify_source

ADAPTER = "scripts/wafl_g1_owner_runtime_adapter.mjs"
REF = "aqqarddfmdfyzeonfgwl"
SHA = re.compile(r"[a-f0-9]{40}\Z")


class Blocked(Exception):
    """Fixed failure code only; no Apple subjects or private DB evidence."""


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
        raise Blocked("WAFL_G1_LIVE_GIT_UNVERIFIED") from None


def admit(repo: Path, sha: str, ref: str, blob: str) -> Path:
    require(ref == REF and SHA.fullmatch(sha) is not None,
            "WAFL_G1_LIVE_TARGET_UNREGISTERED")
    require(SHA.fullmatch(blob) is not None, "WAFL_G1_LIVE_ADAPTER_UNPINNED")
    verify_source(repo, sha, ref)
    path = repo / ADAPTER
    require(path.is_file() and not path.is_symlink()
            and path.resolve().is_relative_to(repo.resolve()),
            "WAFL_G1_LIVE_ADAPTER_MISSING")
    require(git(repo, "rev-parse", "HEAD") == sha
            and git(repo, "rev-parse", f"{sha}:{ADAPTER}") == blob
            and git(repo, "hash-object", ADAPTER) == blob,
            "WAFL_G1_LIVE_ADAPTER_CHANGED")
    return path


def verify_evidence(data: object, sha: str) -> None:
    require(isinstance(data, dict) and data.get("schema_version") == 1
            and data.get("source_sha") == sha and data.get("project_ref") == REF
            and data.get("status") == "INDEPENDENTLY_VERIFIED",
            "WAFL_G1_LIVE_RECEIPT_UNVERIFIED")
    phases = data.get("phases")
    require(isinstance(phases, dict), "WAFL_G1_LIVE_PHASES_MISSING")
    for name in ("apple_auth", "identity_map", "rls_rpc",
                 "neon_50", "domain_parity", "cleanup"):
        require(isinstance(phases.get(name), dict)
                and phases[name].get("status") == "PASS"
                and phases[name].get("real_provider_readback") is True,
                "WAFL_G1_LIVE_PHASE_NOT_VERIFIED")
    subjects = phases["apple_auth"].get("subject_sha256")
    require(isinstance(subjects, list) and len(subjects) == 2
            and len(set(subjects)) == 2
            and all(isinstance(s, str) and re.fullmatch(r"[a-f0-9]{64}", s)
                    for s in subjects),
            "WAFL_G1_APPLE_DISTINCT_SUBJECTS_UNVERIFIED")
    require(phases["identity_map"].get("audited_bijection") is True
            and phases["rls_rpc"].get("independent_sessions") is True
            and phases["rls_rpc"].get("cross_label_denied") is True,
            "WAFL_G1_IDENTITY_RLS_UNVERIFIED")
    neon = phases["neon_50"]
    require(neon.get("count") == 50 and neon.get("fk") == 46
            and neon.get("check") == 4
            and neon.get("readback_per_invariant") is True
            and neon.get("bounded_violations") == 0
            and phases["domain_parity"].get("fingerprints_equal") is True
            and phases["cleanup"].get("independent_zero_residue") is True,
            "WAFL_G1_NEON_PARITY_CLEANUP_UNVERIFIED")
    # A product-generated JSON cannot attest its own independent observer.
    # All-green fields alone never constitute native Apple/Neon LIVE proof.
    raise Blocked("WAFL_G1_INDEPENDENT_READBACK_VERIFIER_NOT_REGISTERED")



def source_preflight(repo: Path, sha: str, ref: str, blob: str) -> str:
    """Exercise only the pinned SOURCE CLI in a disposable product tmp/ directory.

    WAFL's CLI rejects absolute evidence paths. No credential or Owner approval
    flag is forwarded to the child, and no source receipt can become LIVE PASS.
    """
    adapter = admit(repo, sha, ref, blob)
    parent = repo / "tmp"
    created_parent = not parent.exists()
    if created_parent:
        parent.mkdir(mode=0o700)
    require(parent.is_dir() and not parent.is_symlink()
            and parent.resolve().is_relative_to(repo.resolve()),
            "WAFL_G1_SOURCE_PROBE_PATH_UNSAFE")
    try:
        with tempfile.TemporaryDirectory(prefix=".g1-source-", dir=parent) as folder:
            relative = f"tmp/{Path(folder).name}/source.json"
            receipt = Path(folder) / "source.json"
            try:
                result = subprocess.run(
                    ["node", str(adapter), "--source-sha", sha, "--project-ref", ref,
                     "--evidence", relative],
                    cwd=repo, env={"PATH": os.environ.get("PATH", ""), "NODE_ENV": "test"},
                    check=False, timeout=60,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise Blocked("WAFL_G1_SOURCE_PROBE_FAILED") from None
            require(result.returncode == 0 and receipt.is_file()
                    and not receipt.is_symlink() and receipt.stat().st_size <= 4096,
                    "WAFL_G1_SOURCE_PROBE_FAILED")
            try:
                data = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise Blocked("WAFL_G1_SOURCE_PROBE_INVALID") from None
            require(data == {
                "sourceSha": sha,
                "projectRef": ref,
                "runtime": "NOT_VERIFIED",
                "neon": "NOT_VERIFIED",
                "G1_ACCEPT": False,
            }, "WAFL_G1_SOURCE_RECEIPT_NOT_FAIL_CLOSED")
    finally:
        if created_parent:
            try:
                parent.rmdir()
            except OSError:
                pass  # Never remove an existing or nonempty product tmp directory.
    return "WAFL_G1_SOURCE_CONTRACT_PASS_LIVE_NOT_RUN"

def execute(repo: Path, sha: str, ref: str, blob: str,
            evidence: Path, approved: bool) -> str:
    admit(repo, sha, ref, blob)
    if not approved:
        return "WAFL_G1_ADAPTER_SOURCE_CHECK_ONLY_NOT_LIVE"
    require(os.getenv("GITHUB_ACTIONS") == "true"
            and os.getenv("WONJAE_OWNER_LIVE_APPROVED") == "yes",
            "WAFL_G1_OWNER_NOT_AUTHENTICATED")
    require(bool(os.getenv("WAFL_SUPABASE_ACCESS_TOKEN")),
            "WAFL_G1_DEV_PROVIDER_CONNECTION_NOT_READY")
    # Source-only CLI has no trusted in-process Apple/Neon host. Never pass a
    # LIVE-authorized credential context to that CLI or mistake NOT_VERIFIED for
    # externally observed runtime evidence. A separately reviewed host is needed.
    raise Blocked("WAFL_G1_TRUSTED_LIVE_HOST_NOT_REGISTERED")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--project-ref", required=True)
    p.add_argument("--adapter-blob", required=True)
    p.add_argument("--evidence", required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--source-preflight", action="store_true")
    args = p.parse_args()
    try:
        repo = Path(args.repo).resolve()
        if args.source_preflight:
            result = source_preflight(repo, args.source_sha, args.project_ref,
                                      args.adapter_blob)
        else:
            result = execute(repo, args.source_sha, args.project_ref,
                             args.adapter_blob, Path(args.evidence), args.execute)
    except Exception as exc:
        code = str(exc) if isinstance(exc, Blocked) else "WAFL_G1_LIVE_NOT_VERIFIED"
        print("WAFL_G1_LIVE_BLOCKED code=" + code, flush=True)
        return 1
    print(result, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
