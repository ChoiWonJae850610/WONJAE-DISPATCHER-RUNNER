#!/usr/bin/env python3
"""Read-only exact-SHA reconciliation of a previously attached recovery-doc commit.

Runs only after a separately authenticated SANJINWORKS Owner request. No Git
checkout, commit, push, merge, source implementation, provider or release occurs.
A prior failed attach workflow is never retroactively relabelled successful.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import quote

import yaml
from attach_recovery_docs import check_scope, checked_source_pr, verify_manual_gate
from document_pr_merge import (
    DocumentMergeError,
    branch_sha,
    canonical_runs,
    check_files,
    check_pr,
    check_pr_head_validation,
    check_sha,
    request_json,
    route_from_registry,
)

RUNNER = "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER"
TRUSTED_ATTACH_AUTHOR = "WONJAE trusted recovery docs"
TRUSTED_ATTACH_EMAIL = "trusted-recovery@users.noreply.github.com"


def exact_attach_run_name(args: argparse.Namespace) -> str:
    return (
        f"Recovery Docs {args.project} source #{args.source_pr} {args.source_head} "
        f"docs #{args.docs_pr} {args.docs_head} run {args.failed_run_id} "
        f"base {args.base} control {args.control_sha}"
    )


def request_runner_json(endpoint: str) -> object:
    token = os.environ.get("RUNNER_READ_TOKEN", "")
    if not token:
        raise DocumentMergeError("runner read credential unavailable")
    env = {**os.environ, "GH_TOKEN": token}
    result = subprocess.run(
        ["gh", "api", "--method", "GET", endpoint],
        env=env, capture_output=True, text=True, check=False, timeout=25,
    )
    if result.returncode:
        raise DocumentMergeError("trusted prior attach run read failed")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise DocumentMergeError("trusted prior attach run JSON invalid") from exc


def check_failed_attach_run(run: object, args: argparse.Namespace) -> None:
    if (
        not isinstance(run, dict)
        or run.get("id") != args.failed_attach_run_id
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or run.get("head_branch") != "main"
        or run.get("display_title") != exact_attach_run_name(args)
        or str(run.get("path", "")).split("@", 1)[0]
        != ".github/workflows/source-pr-recovery-docs.yml"
    ):
        raise DocumentMergeError("failed attach run is not the exact approved attempt")


def check_attached_commit(
    source: object,
    commit: object,
    route: dict[str, str],
    args: argparse.Namespace,
    paths: list[str],
) -> None:
    if not isinstance(source, dict) or not isinstance(commit, dict):
        raise DocumentMergeError("missing PR/attached commit evidence")
    if source.get("head", {}).get("sha") != args.attached_head:
        raise DocumentMergeError("source PR head differs from exact attached commit")
    # Re-apply the original one-writer/run/branch binding to the original parent,
    # after proving the current head is exactly the one requested by Owner.
    original = {
        **source,
        "head": {**source["head"], "sha": args.source_head},
    }
    checked_source_pr(
        original, route, args.source_pr, args.source_head, args.base,
        args.failed_run_id,
    )
    if source.get("user", {}).get("login") not in (
        "ChoiWonJae850610", "github-actions[bot]"
    ):
        raise DocumentMergeError("source PR author is untrusted")
    parents = commit.get("parents")
    if (
        commit.get("sha") != args.attached_head
        or not isinstance(parents, list)
        or len(parents) != 1
        or parents[0].get("sha") != args.source_head
    ):
        raise DocumentMergeError("attached commit does not directly extend old source PR")
    metadata = commit.get("commit")
    author = metadata.get("author") if isinstance(metadata, dict) else None
    committer = metadata.get("committer") if isinstance(metadata, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("message")
        != f"docs: attach recovery PR #{args.docs_pr} to source PR #{args.source_pr}"
        or not isinstance(author, dict)
        or author.get("name") != TRUSTED_ATTACH_AUTHOR
        or author.get("email") != TRUSTED_ATTACH_EMAIL
        or not isinstance(committer, dict)
        or committer.get("name") != TRUSTED_ATTACH_AUTHOR
        or committer.get("email") != TRUSTED_ATTACH_EMAIL
    ):
        raise DocumentMergeError("attachment commit provenance is not trusted")
    files = commit.get("files")
    if (
        not isinstance(files, list)
        or len(files) != 2
        or len(set(paths)) != 2
        or {item.get("filename") for item in files if isinstance(item, dict)}
        != set(paths)
        or any(
            not isinstance(item, dict)
            or item.get("status") != "modified"
            or item.get("previous_filename")
            for item in files
        )
    ):
        raise DocumentMergeError("attached commit has unexpected source or file edits")


def exact_blob(repo: str, path: str, sha: str) -> tuple[str, bytes]:
    value = request_json([
        f"repos/{repo}/contents/{quote(path, safe='/')}?ref={sha}"
    ])
    if (
        not isinstance(value, dict)
        or value.get("type") != "file"
        or value.get("encoding") != "base64"
        or not isinstance(value.get("content"), str)
        or not isinstance(value.get("size"), int)
        or value["size"] > 400_000
        or value["size"] < 0
    ):
        raise DocumentMergeError("exact regular document content is missing")
    blob = check_sha(value.get("sha"), "document blob SHA")
    try:
        payload = base64.b64decode(value["content"], validate=False)
    except (ValueError, binascii.Error) as exc:
        raise DocumentMergeError("exact document content is not base64") from exc
    if len(payload) != value["size"]:
        raise DocumentMergeError("document byte count changed")
    return blob, payload


def check_exact_documents(
    repo: str, args: argparse.Namespace, paths: list[str],
    state_path: str,
) -> None:
    for path in paths:
        base_sha, original = exact_blob(repo, path, args.base)
        old_sha, old = exact_blob(repo, path, args.source_head)
        doc_sha, doc = exact_blob(repo, path, args.docs_head)
        attached_sha, attached = exact_blob(repo, path, args.attached_head)
        if old_sha != base_sha or old != original:
            raise DocumentMergeError("source PR previously edited trusted document")
        if attached_sha != doc_sha or attached != doc:
            raise DocumentMergeError("attached document differs from reviewed docs PR")
        if path == state_path:
            try:
                check_scope(
                    original.decode("utf-8"), doc.decode("utf-8"),
                    args.source_pr, args.source_head, args.failed_run_id,
                )
            except (UnicodeError, yaml.YAMLError) as exc:
                raise DocumentMergeError("document state encoding/schema invalid") from exc


def reconcile(args: argparse.Namespace) -> int:
    for name in ("source_head", "docs_head", "attached_head", "base", "control_sha"):
        check_sha(getattr(args, name), name)
    if (
        args.source_pr < 1 or args.docs_pr < 1 or args.source_pr == args.docs_pr
        or args.failed_run_id < 1 or args.failed_attach_run_id < 1
        or args.source_head == args.attached_head
        or not os.environ.get("GH_TOKEN")
    ):
        raise DocumentMergeError("reconciliation input/identity invalid")

    registry = yaml.safe_load(Path(args.registry).read_text(encoding="utf-8"))
    route = route_from_registry(registry, args.project)
    repo = route["repo"]
    if branch_sha(repo, route["branch"]) != args.base:
        raise DocumentMergeError("active product branch changed")

    source = request_json([f"repos/{repo}/pulls/{args.source_pr}"])
    docs = request_json([f"repos/{repo}/pulls/{args.docs_pr}"])
    check_pr(docs, route, args.docs_pr, args.docs_head, args.base)
    if not str(docs["head"].get("ref", "")).startswith("docs/"):
        raise DocumentMergeError("reviewed PR is not on docs branch")
    if docs.get("changed_files") != 2:
        raise DocumentMergeError("reviewed PR file count changed")
    files = request_json([f"repos/{repo}/pulls/{args.docs_pr}/files?per_page=100"])
    check_files(files, 2, route["state_path"])
    paths = [file["filename"] for file in files]
    if (
        len(paths) != 2
        or paths.count(route["state_path"]) != 1
        or len([p for p in paths if p.startswith("docs/")]) != 1
        or any(item["status"] != "modified" for item in files)
    ):
        raise DocumentMergeError("reconciliation requires two modified documents")

    attached_commit = request_json([f"repos/{repo}/commits/{args.attached_head}"])
    check_attached_commit(source, attached_commit, route, args, paths)
    open_prs = request_json([
        f"repos/{repo}/pulls?state=open&base={route['branch']}&per_page=100"
    ])
    if (
        not isinstance(open_prs, list)
        or len(open_prs) >= 100
        or sorted(
            pr["number"] for pr in open_prs
            if str(pr.get("head", {}).get("ref", "")).startswith(("direct/", "job/"))
        ) != [args.source_pr]
    ):
        raise DocumentMergeError("single source writer is not unique")

    check_pr_head_validation(
        canonical_runs(repo, route["workflow"], args.docs_head, "pull_request"),
        route, args.docs_head,
    )
    verify_manual_gate(
        repo, args.source_pr, args.source_head, args.failed_run_id, args.project,
    )
    prior = request_runner_json(
        f"repos/{RUNNER}/actions/runs/{args.failed_attach_run_id}"
    )
    check_failed_attach_run(prior, args)
    check_exact_documents(repo, args, paths, route["state_path"])

    attached_run_id = check_pr_head_validation(
        canonical_runs(repo, route["workflow"], args.attached_head, "pull_request"),
        route, args.attached_head,
    )
    # Re-check the exact HEADs at the end, rather than trusting a stale UI,
    # cached status, older PR-head CI or an unrelated success run.
    if branch_sha(repo, route["branch"]) != args.base:
        raise DocumentMergeError("product base changed during reconciliation")
    check_attached_commit(
        request_json([f"repos/{repo}/pulls/{args.source_pr}"]),
        attached_commit, route, args, paths,
    )
    check_pr(
        request_json([f"repos/{repo}/pulls/{args.docs_pr}"]),
        route, args.docs_pr, args.docs_head, args.base,
    )
    live_prs = request_json([
        f"repos/{repo}/pulls?state=open&base={route['branch']}&per_page=100"
    ])
    if (
        not isinstance(live_prs, list) or len(live_prs) >= 100
        or sorted(
            pr["number"] for pr in live_prs
            if str(pr.get("head", {}).get("ref", "")).startswith(("direct/", "job/"))
        ) != [args.source_pr]
    ):
        raise DocumentMergeError("source writer inventory changed during reconciliation")
    if check_pr_head_validation(
        canonical_runs(repo, route["workflow"], args.attached_head, "pull_request"),
        route, args.attached_head,
    ) != attached_run_id:
        raise DocumentMergeError("latest exact attached-SHA canonical CI changed")
    print(
        f"RECOVERY_DOCS_RECONCILED project={args.project} "
        f"source_pr={args.source_pr} source_parent={args.source_head} "
        f"attached_head={args.attached_head} docs_pr={args.docs_pr} "
        f"docs_head={args.docs_head} pr_head_ci_run={attached_run_id} "
        "read_only=true resume=SEPARATE_OWNER_ACTION"
    )
    return attached_run_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-pr", type=int, required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--attached-head", required=True)
    parser.add_argument("--docs-pr", type=int, required=True)
    parser.add_argument("--docs-head", required=True)
    parser.add_argument("--failed-run-id", type=int, required=True)
    parser.add_argument("--failed-attach-run-id", type=int, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--control-sha", required=True)
    args = parser.parse_args()
    try:
        reconcile(args)
    except (DocumentMergeError, yaml.YAMLError) as exc:
        # No GitHub response bodies, private file contents or token-bearing logs.
        print(f"RECOVERY_DOCS_RECONCILE_HALTED reason={type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
