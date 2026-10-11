#!/usr/bin/env python3
"""Fail-closed, exact-identity integration of one Owner-approved common docs PR.

Disabled unless a separately authorized main workflow, one repository-scoped token,
one configured target, and a matching short-lived SANJINWORKS Owner HMAC exist.
This never generates source, changes credentials or invokes provider/product jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
NONCE = re.compile(r"^[0-9a-f]{32}$")
REPO = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$")
SAFE_PATH = re.compile(
    r"^(?:docs/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\.md"
    r"|(?:src|tests)/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\.(?:py|js|mjs|ts|tsx))$"
)
SENSITIVE = re.compile(
    r"(?:^|[/._-])(?:auth|security|credential|secret|token|permission|policy|policies|"
    r"production|deploy|deployment|release|owner-action|common-system-owner|"
    r"session|workflow|runner|worker|direct-worker|provider|dispatcher|operating|"
    r"execution|bootstrap|registry|control|merge|write)(?:[/._-]|$)",
    re.IGNORECASE,
)
WORKFLOWS = {
    "DEV-CONTROL": ("control-validation.yml", "DEV-CONTROL Validation"),
    "WONJAE-DISPATCHER-RUNNER": ("validate.yml", "Runner Validation"),
}
VERSION = "common-pr-merge-v1"
# One repository-scoped merge token at a time. SANJINWORKS/Production and
# security/credential/workflow-policy changes retain independent approval.
MAX_REVIEWED_FILES = 30
MAX_PATCH_CHARS = 24000
MAX_TOTAL_PATCH_CHARS = 120000


class AdmissionError(RuntimeError):
    """The workflow must refuse integration; no upstream error content is logged."""


def exact_sha(value: Any) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise AdmissionError("malformed SHA identity")
    return value


def canonical(args: argparse.Namespace) -> bytes:
    values = [
        VERSION, args.repository, str(args.pr_number), args.head, args.base,
        str(args.ci_run_id), args.paths_digest, args.diff_digest,
        str(args.expires_ms), args.nonce,
    ]
    return "\n".join(values).encode("utf-8")


def verify_owner_receipt(args: argparse.Namespace, secret: str, now_ms: int | None = None) -> None:
    if not secret or len(secret) < 32:
        raise AdmissionError("independent Owner attestation is not configured")
    if not DIGEST.fullmatch(args.approval_mac) or not NONCE.fullmatch(args.nonce):
        raise AdmissionError("invalid Owner attestation format")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if not isinstance(args.expires_ms, int) or not now < args.expires_ms <= now + 15 * 60 * 1000:
        raise AdmissionError("Owner attestation expired or unbounded")
    expected = hmac.new(secret.encode("utf-8"), canonical(args), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, args.approval_mac):
        raise AdmissionError("Owner attestation mismatch")


def guard_scope(args: argparse.Namespace, env: dict[str, str]) -> tuple[str, str]:
    exact_sha(args.head)
    exact_sha(args.base)
    if (not isinstance(args.pr_number, int) or not 1 <= args.pr_number <= 1_000_000
        or not isinstance(args.ci_run_id, int) or args.ci_run_id < 1
        or not isinstance(args.repository, str) or not REPO.fullmatch(args.repository)
        or not isinstance(args.paths_digest, str) or not DIGEST.fullmatch(args.paths_digest)
        or not isinstance(args.diff_digest, str) or not DIGEST.fullmatch(args.diff_digest)):
        raise AdmissionError("malformed exact PR admission")
    target = env.get("COMMON_PR_MERGE_TARGET_REPOSITORY", "")
    if not target or target != args.repository:
        raise AdmissionError("target repository is not separately activated")
    if args.repository.split("/")[0] != env.get("GITHUB_REPOSITORY_OWNER", ""):
        raise AdmissionError("Owner repository identity mismatch")
    name = args.repository.split("/")[1]
    # SANJINWORKS merges always require an additional Production-effect receipt;
    # no such mechanism is authorized in this first limited docs-only slice.
    if name == "SANJINWORKS":
        raise AdmissionError("SANJINWORKS Production-effect gate not implemented")
    if name not in WORKFLOWS:
        raise AdmissionError("no matching common-system verification contract")
    if env.get("COMMON_PR_MERGE_ACTIVATED") != "true":
        raise AdmissionError("trusted common PR executor is not activated")
    if not env.get("GH_TOKEN") or not env.get("COMMON_PR_MERGE_APPROVAL_SECRET"):
        raise AdmissionError("independent merge credential or attestation is missing")
    return WORKFLOWS[name]


def checked_pr(pr: Any, args: argparse.Namespace) -> str:
    if not isinstance(pr, dict) or pr.get("number") != args.pr_number:
        raise AdmissionError("selected PR number changed")
    if pr.get("state") != "open" or pr.get("merged_at") or pr.get("draft") is not False:
        raise AdmissionError("PR is not currently open and final")
    if pr.get("mergeable") is not True:
        raise AdmissionError("current PR mergeability unverified")
    if pr.get("user", {}).get("login") != args.repository.split("/")[0]:
        raise AdmissionError("untrusted PR author")
    base, head = pr.get("base"), pr.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise AdmissionError("PR base/head evidence missing")
    if base.get("ref") != "main" or base.get("repo", {}).get("full_name") != args.repository:
        raise AdmissionError("PR target is not the exact common main")
    if head.get("repo", {}).get("full_name") != args.repository or head.get("sha") != args.head:
        raise AdmissionError("PR head changed or belongs to a fork")
    if (not isinstance(head.get("ref"), str)
        or not re.fullmatch(r"[A-Za-z0-9._/-]{1,160}", head["ref"])):
        raise AdmissionError("invalid trusted PR branch")
    count = pr.get("changed_files")
    if type(count) is not int or not 1 <= count <= 100:
        raise AdmissionError("PR changed-file count outside reviewed scope")
    return head["ref"]


def safe_common_path(repo: str, path: Any) -> bool:
    """Small routine-code/docs allowlist, not security or Production approval."""
    if repo not in {
        "ChoiWonJae850610/DEV-CONTROL",
        "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER",
    }:
        return False
    return (
        isinstance(path, str) and bool(SAFE_PATH.fullmatch(path))
        and all(part not in {".", ".."} for part in path.split("/"))
        and not bool(SENSITIVE.search(path))
    )


def check_file_inventory(files: Any, count: int, digest: str,
                         diff_digest: str, repo: str) -> None:
    """Match every Owner-reviewed changed path AND GitHub patch, not just filenames."""
    if (not isinstance(files, list) or not isinstance(count, int)
        or not 1 <= count <= MAX_REVIEWED_FILES or len(files) != count):
        raise AdmissionError("reviewed common PR file inventory is incomplete")
    paths: list[str] = []
    records: list[tuple[str, str]] = []
    total_chars = 0
    for file in files:
        if not isinstance(file, dict) or file.get("status") not in {"added", "modified"}:
            raise AdmissionError("deletion, rename or unknown file mutation requires another gate")
        path, patch = file.get("filename"), file.get("patch")
        changes = file.get("changes")
        if (not safe_common_path(repo, path) or file.get("previous_filename")
            or not isinstance(patch, str) or not patch or len(patch) > MAX_PATCH_CHARS
            or type(changes) is not int or not 1 <= changes <= 2000):
            raise AdmissionError("sensitive/unreviewed path or incomplete diff is blocked")
        total_chars += len(patch)
        paths.append(path)
        records.append((path, f"{path}\n{file['status']}\n{patch}"))
    if len(set(paths)) != count or total_chars > MAX_TOTAL_PATCH_CHARS:
        raise AdmissionError("duplicate or oversized common PR diff")
    observed_paths = hashlib.sha256("\n".join(sorted(paths)).encode("utf-8")).hexdigest()
    observed_diff = hashlib.sha256(
        "\n---\n".join(value for _, value in sorted(records)).encode("utf-8")
    ).hexdigest()
    if (not hmac.compare_digest(observed_paths, digest)
        or not hmac.compare_digest(observed_diff, diff_digest)):
        raise AdmissionError("current GitHub diff differs from exact Owner-reviewed contents")


def exact_ci(payload: Any, args: argparse.Namespace, workflow: str, name: str,
             event: str, sha: str, head_ref: str | None = None) -> int | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise AdmissionError("canonical CI inventory unavailable")
    if payload.get("total_count", 0) > 100:
        raise AdmissionError("canonical CI inventory incomplete")
    eligible = sorted((run for run in payload["workflow_runs"]
        if isinstance(run, dict) and run.get("head_sha") == sha
        and run.get("event") == event and run.get("name") == name
        and str(run.get("path", "")).split("@", 1)[0] == ".github/workflows/" + workflow
        and (head_ref is None or run.get("head_branch") == head_ref)
        and type(run.get("id")) is int),
        key=lambda run: run["id"], reverse=True)
    if not eligible:
        if event == "push":
            return None
        raise AdmissionError("exact PR-head validation missing")
    newest = eligible[0]
    if event == "pull_request" and newest["id"] != args.ci_run_id:
        raise AdmissionError("Owner-reviewed PR-head CI is no longer latest")
    if newest.get("status") != "completed":
        if event == "push":
            return None
        raise AdmissionError("PR-head CI still pending")
    if newest.get("conclusion") != "success":
        raise AdmissionError("exact canonical CI failed")
    return newest["id"]


def gh_api(arguments: list[str]) -> Any:
    try:
        response = subprocess.run(["gh", "api", *arguments],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionError("GitHub transport unavailable") from exc
    if response.returncode != 0:
        raise AdmissionError("GitHub read or guarded merge refused")
    try:
        return json.loads(response.stdout)
    except json.JSONDecodeError as exc:
        raise AdmissionError("GitHub returned malformed evidence") from exc


def main_sha(repo: str) -> str:
    return exact_sha(gh_api([f"repos/{repo}/branches/main"])["commit"]["sha"])


def ci_runs(repo: str, workflow: str, sha: str, event: str) -> Any:
    return gh_api([f"repos/{repo}/actions/workflows/{workflow}/runs"
                   f"?head_sha={sha}&event={event}&per_page=100"])


def integrate(args: argparse.Namespace, env: dict[str, str] | None = None) -> int:
    runtime = dict(os.environ) if env is None else env
    workflow, name = guard_scope(args, runtime)
    verify_owner_receipt(args, runtime["COMMON_PR_MERGE_APPROVAL_SECRET"])

    if main_sha(args.repository) != args.base:
        raise AdmissionError("main moved since Owner reviewed exact PR")
    pr = gh_api([f"repos/{args.repository}/pulls/{args.pr_number}"])
    ref = checked_pr(pr, args)
    files = gh_api([f"repos/{args.repository}/pulls/{args.pr_number}/files?per_page=100"])
    check_file_inventory(
        files, pr["changed_files"], args.paths_digest, args.diff_digest, args.repository
    )
    exact_ci(ci_runs(args.repository, workflow, args.head, "pull_request"),
             args, workflow, name, "pull_request", args.head, ref)
    if main_sha(args.repository) != args.base:
        raise AdmissionError("main moved before exact PR integration")
    # Final immutable pre-write check against a concurrently edited PR.
    latest_pr = gh_api([f"repos/{args.repository}/pulls/{args.pr_number}"])
    checked_pr(latest_pr, args)
    latest_files = gh_api([f"repos/{args.repository}/pulls/{args.pr_number}/files?per_page=100"])
    check_file_inventory(
        latest_files, latest_pr["changed_files"], args.paths_digest, args.diff_digest,
        args.repository
    )
    exact_ci(ci_runs(args.repository, workflow, args.head, "pull_request"),
             args, workflow, name, "pull_request", args.head, ref)

    merged = gh_api(["--method", "PUT", f"repos/{args.repository}/pulls/{args.pr_number}/merge",
                     "-f", "merge_method=merge", "-f", f"sha={args.head}"])
    if not isinstance(merged, dict) or merged.get("merged") is not True:
        raise AdmissionError("GitHub has not confirmed an exact merge")
    integrated = exact_sha(merged.get("sha"))
    commit = gh_api([f"repos/{args.repository}/commits/{integrated}"])
    parents = commit.get("parents", []) if isinstance(commit, dict) else []
    if (commit.get("sha") != integrated or len(parents) != 2
        or parents[0].get("sha") != args.base or parents[1].get("sha") != args.head
        or main_sha(args.repository) != integrated):
        raise AdmissionError("integrated ancestry or branch SHA does not match review")

    integrated_run = None
    for _ in range(72):
        integrated_run = exact_ci(ci_runs(args.repository, workflow, integrated, "push"),
            args, workflow, name, "push", integrated)
        if integrated_run is not None:
            break
        time.sleep(10)
    if integrated_run is None:
        raise AdmissionError("merge occurred but integrated SHA CI is unverified")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(f"## Common-system routine PR integration\n\n"
                      f"- PR: #{args.pr_number}\n- Exact PR head: {args.head}\n"
                      f"- Reviewed base: {args.base}\n- PR-head validation: {args.ci_run_id} PASS\n"
                      f"- Integrated SHA: {integrated}\n"
                      f"- Integrated CI: {integrated_run} PASS\n"
                      "- Production/runtime/device/credentials: NOT_VERIFIED_OR_RUN\n")
    print(f"COMMON_PR_INTEGRATION_VALIDATED pr={args.pr_number} sha={integrated}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--ci-run-id", required=True, type=int)
    parser.add_argument("--paths-digest", required=True)
    parser.add_argument("--diff-digest", required=True)
    parser.add_argument("--expires-ms", required=True, type=int)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--approval-mac", required=True)
    args = parser.parse_args()
    try:
        return integrate(args)
    except (AdmissionError, KeyError, ValueError, TypeError, IndexError) as exc:
        # Never include API error bodies, tokens, or an HMAC value in logs.
        print(f"::error::common PR merge blocked: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
