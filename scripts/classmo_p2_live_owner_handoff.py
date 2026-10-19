#!/usr/bin/env python3
"""Exact CLASSMO P2 Development LIVE adapter handoff, Owner only."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from classmo_editorial_027_p2_live_guard import verify_source

ADAPTER = "scripts/classmo_editorial_p2_live_owner_adapter.py"
REF = "hfclkbqzxiqhcckwknih"
SHA = re.compile(r"[a-f0-9]{40}\Z")


def source_runtime_contract() -> dict[str, object]:
    """Reviewed source-stage seam; NOT an authenticated LIVE attestation.

    Source Codex may implement this interface without provider calls. The trusted
    Owner LIVE workflow must later independently verify every claimed runtime
    and license property. No field here can be used as an acceptance flag.
    """
    return {
        "contract": "CLASSMO_P2_RUNTIME_HANDOFF_V1",
        "stage": "SOURCE_INTERFACE_ONLY_PROVIDER_NOT_RUN",
        "project_ref": REF,
        "environment": "development",
        "gate": "editorial_027_p2_live_acceptance",
        "adapter_path": ADAPTER,
        "plan_git_blob": "2aebbaf9dfa2699c60d14e3e614927d6653103c1",
        "edge_function": "editorial-media-prepare",
        "edge_runtime": "1.76.2",
        "wasm_package": "imagemagick-wasm",
        "wasm_version": "0.0.44",
        "required_live_proof": [
            "authenticated_owner_dispatch_and_exact_integrated_source_sha",
            "registered_project_ref_and_plan_blob_and_adapter_blob",
            "independent_deployed_edge_origin_version_and_asset_digest_readback",
            "fresh_runner_challenge_bound_to_deployment_and_source_sha",
            "cryptographically_verified_deployment_signature_from_reviewed_trust_root",
            "reviewed_server_only_license_reference_and_receipt_binding",
            "genuine_auth_roles_http_storage_and_independent_postgres_readback",
            "exact_synthetic_cleanup_and_non_owned_fingerprint_readback",
        ],
        "runtime_proof": "NOT_VERIFIED",
        "signature_proof": "NOT_VERIFIED",
        "server_license_proof": "NOT_VERIFIED",
        "live_approval": False,
    }


def require_registered_runtime_signature(_evidence: object) -> None:
    """Fail closed until a separately reviewed trusted-main verifier exists.

    The adapter's JSON, its 'signature' string, environment flags and a
    fixture's PASS fields are not cryptographic proofs. This verifier must
    eventually use a reviewed pinned trust root and independently observed
    deployed-runtime readback; never accept a caller-supplied boolean or key.
    """
    raise Blocked("CLASSMO_P2_RUNTIME_SIGNATURE_VERIFIER_NOT_REGISTERED")


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
    # A self-asserted JSON receipt can never sign itself into LIVE acceptance.
    require_registered_runtime_signature(data)



def source_preflight(repo: Path, sha: str, ref: str, blob: str) -> str:
    """Run the pinned CLASSMO SOURCE CLI without credentials or LIVE authority."""
    adapter = admit(repo, sha, ref, blob)
    parent = repo / "tmp"
    created_parent = not parent.exists()
    if created_parent:
        parent.mkdir(mode=0o700)
    require(parent.is_dir() and not parent.is_symlink()
            and parent.resolve().is_relative_to(repo.resolve()),
            "CLASSMO_P2_SOURCE_PROBE_PATH_UNSAFE")
    try:
        with tempfile.TemporaryDirectory(prefix=".p2-source-", dir=parent) as folder:
            relative = f"tmp/{Path(folder).name}/source.json"
            receipt = Path(folder) / "source.json"
            try:
                result = subprocess.run(
                    [sys.executable, str(adapter), "--source-sha", sha, "--project-ref", ref,
                     "--evidence", relative],
                    cwd=repo,
                    env={"PATH": os.environ.get("PATH", ""),
                         "PYTHONDONTWRITEBYTECODE": "1"},
                    check=False, timeout=60,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise Blocked("CLASSMO_P2_SOURCE_PROBE_FAILED") from None
            require(result.returncode == 0 and receipt.is_file()
                    and not receipt.is_symlink() and receipt.stat().st_size <= 262144,
                    "CLASSMO_P2_SOURCE_PROBE_FAILED")
            try:
                data = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise Blocked("CLASSMO_P2_SOURCE_PROBE_INVALID") from None
            handoff = data.get("runtime_handoff") if isinstance(data, dict) else None
            require(isinstance(data, dict)
                    and data.get("schema_version") == 1
                    and data.get("source_sha") == sha
                    and data.get("project_ref") == ref
                    and data.get("status") == "SOURCE_ONLY_NOT_RUNTIME_ACCEPTANCE"
                    and data.get("source_completion") == "INCOMPLETE"
                    and data.get("executable_live_adapter") is False
                    and data.get("owner_gate")
                    == "REGISTERED_DEVELOPMENT_RUNTIME_HANDOFF_REQUIRED"
                    and isinstance(data.get("phases"), dict)
                    and set(data["phases"]) == {
                        "admission", "auth", "runtime_asset", "images", "http_matrix",
                        "db_commit_race", "db_rollback_race", "cleanup"
                    }
                    and all(value == "NOT_RUN" for value in data["phases"].values())
                    and isinstance(handoff, dict)
                    and handoff.get("interface") == "CLASSMO_P2_RUNTIME_HANDOFF_V1"
                    and handoff.get("source_sha") == sha
                    and handoff.get("project_ref") == ref
                    and handoff.get("plan_blob")
                    == "2aebbaf9dfa2699c60d14e3e614927d6653103c1"
                    and handoff.get("status") == "NOT_VERIFIED"
                    and handoff.get("live_admission_available") is False,
                    "CLASSMO_P2_SOURCE_RECEIPT_NOT_FAIL_CLOSED")
    finally:
        if created_parent:
            try:
                parent.rmdir()
            except OSError:
                pass  # Keep any preexisting product tmp state.
    return "CLASSMO_P2_SOURCE_CONTRACT_PASS_LIVE_NOT_RUN"


# Existing project-specific LIVE host. Product source must implement this path
# through an authenticated SANJINWORKS SOURCE_READY task; no generic executor.
HOST = "scripts/classmo_editorial_p2_live_host.py"


def admit_host(repo: Path, sha: str, host_blob: str) -> Path:
    """Require the reviewed exact-integrated product LIVE host, not the source CLI."""
    require(SHA.fullmatch(host_blob) is not None,
            "CLASSMO_P2_TRUSTED_HOST_NOT_REGISTERED")
    host = repo / HOST
    require(host.is_file() and not host.is_symlink()
            and host.resolve().is_relative_to(repo.resolve()),
            "CLASSMO_P2_TRUSTED_HOST_NOT_INTEGRATED")
    require(git(repo, "rev-parse", "HEAD") == sha
            and git(repo, "rev-parse", f"{sha}:{HOST}") == host_blob
            and git(repo, "hash-object", HOST) == host_blob,
            "CLASSMO_P2_TRUSTED_HOST_BLOB_MISMATCH")
    return host


def launch_host(repo: Path, sha: str, ref: str, host: Path,
                evidence: Path) -> dict:
    """Run the pinned product LIVE host only in an authenticated Owner job.

    The product host may COLLECT real readbacks. Runner acceptance still
    requires independent verified evidence; CLI exit 0 is not LIVE PASS.
    """
    run_id = os.getenv("GITHUB_RUN_ID", "")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run_id) is not None,
            "CLASSMO_P2_OWNER_RUN_ID_NOT_REGISTERED")
    temporary = Path(os.getenv("RUNNER_TEMP", "/unregistered-runner-temp"))
    require(temporary.is_dir() and not temporary.is_symlink()
            and evidence.parent.resolve() == temporary.resolve()
            and evidence.suffix == ".json" and not evidence.is_symlink()
            and not evidence.exists(), "CLASSMO_P2_LIVE_RECEIPT_PATH_UNSAFE")
    # Fixed allowlist: never inherit arbitrary runner secrets, user URLs or
    # dynamic environment flags into the product. Credential presence alone
    # is not independent Provider or signer acceptance.
    allow = ("PATH", "GITHUB_ACTIONS", "GITHUB_RUN_ID",
             "WONJAE_OWNER_LIVE_APPROVED", "CLASSMO_SUPABASE_ACCESS_TOKEN",
             "CLASSMO_G1V_DEV_DB_PASSWORD", "CLASSMO_G1V_DEV_DB_CA_PEM")
    env = {key: os.environ[key] for key in allow if key in os.environ}
    try:
        result = subprocess.run(
            [sys.executable, str(host), "--source-sha", sha,
             "--project-ref", ref, "--run-id", run_id,
             "--evidence", str(evidence)],
            cwd=repo, env=env, check=False, timeout=900,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("CLASSMO_P2_TRUSTED_HOST_EXECUTION_FAILED") from None
    require(result.returncode == 0 and evidence.is_file()
            and not evidence.is_symlink() and 0 < evidence.stat().st_size <= 262144,
            "CLASSMO_P2_LIVE_RECEIPT_NOT_COLLECTED")
    try:
        data = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Blocked("CLASSMO_P2_LIVE_RECEIPT_INVALID") from None
    require(isinstance(data, dict) and data.get("source_sha") == sha
            and data.get("project_ref") == ref
            and data.get("owner_run_id") == run_id,
            "CLASSMO_P2_LIVE_RECEIPT_IDENTITY_MISMATCH")
    return data

def execute(repo: Path, sha: str, ref: str, blob: str,
            evidence: Path, approved: bool, host_blob: str = "") -> str:
    admit(repo, sha, ref, blob)
    if not approved:
        return "CLASSMO_P2_ADAPTER_SOURCE_CHECK_ONLY_NOT_LIVE"
    require(os.getenv("GITHUB_ACTIONS") == "true"
            and os.getenv("WONJAE_OWNER_LIVE_APPROVED") == "yes",
            "CLASSMO_P2_OWNER_NOT_AUTHENTICATED")
    require(bool(os.getenv("CLASSMO_SUPABASE_ACCESS_TOKEN"))
            and bool(os.getenv("CLASSMO_G1V_DEV_DB_PASSWORD")),
            "CLASSMO_P2_DEV_PROVIDER_CONNECTION_NOT_READY")
    host = admit_host(repo, sha, host_blob)
    data = launch_host(repo, sha, ref, host, evidence)
    # Independently verified deployment identity, signed Runtime/WASM digest
    # and server-only license trust root still require reviewed Runner proof.
    verify_evidence(data, sha)
    return "CLASSMO_P2_LIVE_EVIDENCE_REVIEWED_DEVICE_QA_NOT_RUN"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--project-ref", required=True)
    p.add_argument("--adapter-blob", required=True)
    p.add_argument("--host-blob", default="")
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
                             args.adapter_blob, Path(args.evidence), args.execute,
                             args.host_blob)
    except Exception as exc:
        code = str(exc) if isinstance(exc, Blocked) else "CLASSMO_P2_LIVE_NOT_VERIFIED"
        print("CLASSMO_P2_LIVE_BLOCKED code=" + code, flush=True)
        return 1
    print(result, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
