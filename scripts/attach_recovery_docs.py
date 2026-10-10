#!/usr/bin/env python3
"""Attach exact Owner-approved documentation to ONE existing Direct Worker PR.

Never merge the source or documentation PR here. Only a signed-in SANJINWORKS
Owner dispatch may run this trusted-main, project-token-scoped operation.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path

import yaml
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


def checked_source_pr(pr: object, route: dict, number: int, sha: str,
                      base: str, failed_run_id: int) -> str:
    if not isinstance(pr, dict) or pr.get("number") != number:
        raise DocumentMergeError("source PR number mismatch")
    head, target = pr.get("head"), pr.get("base")
    if not isinstance(head, dict) or not isinstance(target, dict):
        raise DocumentMergeError("source PR refs unavailable")
    branch = head.get("ref")
    if not isinstance(branch, str) or not re.fullmatch(
        r"direct/[A-Za-z0-9][A-Za-z0-9_/-]{0,100}", branch
    ) or ".." in branch or "//" in branch or branch.endswith("/"):
        raise DocumentMergeError("not an existing Direct Worker branch")
    if (pr.get("state") != "open" or pr.get("merged_at") is not None
        or pr.get("draft") is not False or head.get("sha") != sha
        or head.get("repo", {}).get("full_name") != route["repo"]
        or target.get("ref") != route["branch"] or target.get("sha") != base
        or target.get("repo", {}).get("full_name") != route["repo"]):
        raise DocumentMergeError("source PR/base/head identity changed")
    body = pr.get("body")
    if (not isinstance(body, str) or
        f"- source_base_sha: `{base}`" not in body or
        f"- runner_run_id: `{failed_run_id}`" not in body or
        f"- project: `{route['repo'].split('/')[-1]}`" not in body):
        raise DocumentMergeError("original Direct Worker run binding missing")
    return branch


def verify_manual_gate(repo: str, source_pr: int, source_sha: str,
                       failed_run_id: int, project: str) -> None:
    token = os.environ.get("RUNNER_READ_TOKEN", "")
    if not token:
        raise DocumentMergeError("trusted runner read authority unavailable")
    result = subprocess.run(
        ["gh", "api", "repos/ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER/actions/runs/"
         + str(failed_run_id)],
        env={**os.environ, "GH_TOKEN": token}, capture_output=True,
        text=True, timeout=25, check=False,
    )
    if result.returncode:
        raise DocumentMergeError("exact failed Runner run cannot be verified")
    import json
    try:
        run = json.loads(result.stdout)
    except ValueError as exc:
        raise DocumentMergeError("malformed Runner run receipt") from exc
    if (run.get("id") != failed_run_id or run.get("status") != "completed"
        or run.get("conclusion") != "failure" or run.get("event") != "workflow_dispatch"
        or not str(run.get("display_title", "")).startswith(f"Direct Worker {project} ")):
        raise DocumentMergeError("not the terminal exact Direct Worker run")
    comments = request_json([f"repos/{repo}/issues/{source_pr}/comments?per_page=100"])
    if not isinstance(comments, list) or len(comments) >= 100:
        raise DocumentMergeError("source PR manual evidence inventory ambiguous")
    if not any(
        isinstance(comment, dict)
        and comment.get("user", {}).get("login") in
            ("ChoiWonJae850610", "github-actions[bot]")
        and "- result: `MANUAL_REQUIRED`" in str(comment.get("body", ""))
        and f"- runner_run_id: `{failed_run_id}`" in str(comment.get("body", ""))
        and f"- head_sha: `{source_sha}`" in str(comment.get("body", ""))
        for comment in comments
    ):
        raise DocumentMergeError("exact Owner/manual gate comment not present")


def check_scope(original: str, doc: str, source_pr: int,
                source_sha: str, failed_run_id: int) -> None:
    old, proposed = yaml.safe_load(original), yaml.safe_load(doc)
    if not isinstance(old, dict) or not isinstance(proposed, dict):
        raise DocumentMergeError("product execution state must be a mapping")
    if old.get("schema_version") != 1 or proposed.get("schema_version") != 1:
        raise DocumentMergeError("unsupported execution-state schema")
    old_action, new_action = old.get("next_action"), proposed.get("next_action")
    if not isinstance(old_action, dict) or not isinstance(new_action, dict):
        raise DocumentMergeError("source action missing")
    old_scope, new_scope = old_action.get("source_scope"), new_action.get("source_scope")
    if (old_action.get("type") != "SOURCE_READY"
        or not isinstance(old_scope, list) or not old_scope
        or not isinstance(new_scope, list) or len(new_scope) != len(old_scope) + 1
        or new_scope[:-1] != old_scope):
        raise DocumentMergeError("recovery must append exactly one source-scope entry")
    extra = new_scope[-1]
    if (not isinstance(extra, str) or len(extra) > 2600
        or f"PR #{source_pr}" not in extra or source_sha not in extra
        or str(failed_run_id) not in extra
        or len(set(re.findall(r"tests/[A-Za-z0-9_./-]+\.mjs", extra))) != 1):
        raise DocumentMergeError("narrow exact-PR/test/manual exception missing")
    remaining_old = {k: v for k, v in old_action.items() if k != "source_scope"}
    remaining_new = {k: v for k, v in new_action.items() if k != "source_scope"}
    if remaining_old != remaining_new:
        raise DocumentMergeError("recovery changed task identity or other action fields")
    if ({k: v for k, v in old.items() if k != "next_action"}
        != {k: v for k, v in proposed.items() if k != "next_action"}):
        raise DocumentMergeError("recovery modified successor queue or non-scope authority")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        timeout=50, check=False,
    )
    if result.returncode:
        raise DocumentMergeError("trusted Git checkout, scope or push operation failed")
    return result.stdout.strip()


def regular_tree_file(root: Path, sha: str, path: str) -> None:
    details = git(root, "ls-tree", sha, "--", path).split("\t", 1)
    if len(details) != 2 or details[1] != path or not details[0].startswith("100644 blob "):
        raise DocumentMergeError("recovery path is not an exact regular Git file")


def wait_attached_pr_head(
    repo: str, route: dict, args: argparse.Namespace, attached: str,
    source_branch: str, attempts: int = 18, interval: int = 3,
) -> None:
    """Wait through GitHub PR-ref propagation; never accept a different writer."""
    for attempt in range(attempts):
        pr = request_json([f"repos/{repo}/pulls/{args.source_pr}"])
        if (
            not isinstance(pr, dict)
            or pr.get("state") != "open"
            or pr.get("merged_at") is not None
            or pr.get("base", {}).get("sha") != args.base
            or pr.get("base", {}).get("ref") != route["branch"]
            or pr.get("head", {}).get("ref") != source_branch
        ):
            raise DocumentMergeError("post-push PR identity changed")
        observed = pr.get("head", {}).get("sha")
        if observed == attached:
            return
        if observed != args.source_head:
            raise DocumentMergeError("post-push PR head moved to an unapproved SHA")
        if attempt + 1 < attempts:
            time.sleep(interval)
    raise DocumentMergeError("POST_PUSH_READBACK_DELAY")


def attach(args: argparse.Namespace) -> None:
    for name in ("source_head", "docs_head", "base", "control_sha"):
        check_sha(getattr(args, name), name)
    if (args.source_pr < 1 or args.docs_pr < 1
        or args.source_pr == args.docs_pr or args.failed_run_id < 1):
        raise DocumentMergeError("invalid exact recovery identities")
    if not os.environ.get("GH_TOKEN"):
        raise DocumentMergeError("project-scoped token missing")
    registry = yaml.safe_load(Path(args.registry).read_text(encoding="utf-8"))
    route = route_from_registry(registry, args.project)
    repo = route["repo"]
    if branch_sha(repo, route["branch"]) != args.base:
        raise DocumentMergeError("product active branch moved")
    source = request_json([f"repos/{repo}/pulls/{args.source_pr}"])
    source_branch = checked_source_pr(
        source, route, args.source_pr, args.source_head, args.base, args.failed_run_id,
    )
    docs = request_json([f"repos/{repo}/pulls/{args.docs_pr}"])
    check_pr(docs, route, args.docs_pr, args.docs_head, args.base)
    if not str(docs["head"]["ref"]).startswith("docs/"):
        raise DocumentMergeError("recovery scope requires a documentation branch")
    if docs["changed_files"] != 2:
        raise DocumentMergeError("recovery document PR must change exactly two files")
    files = request_json([f"repos/{repo}/pulls/{args.docs_pr}/files?per_page=100"])
    check_files(files, 2, route["state_path"])
    paths = [entry["filename"] for entry in files]
    if (paths.count(route["state_path"]) != 1
        or len([path for path in paths if path.startswith("docs/")]) != 1
        or any(entry["status"] != "modified" for entry in files)):
        raise DocumentMergeError("recovery requires only one state and one docs modification")
    changes = request_json([f"repos/{repo}/pulls/{args.source_pr}/files?per_page=100"])
    if (not isinstance(changes, list) or len(changes) != source.get("changed_files")
        or any(item.get("filename") in paths for item in changes)):
        raise DocumentMergeError("source PR independently changed a recovery document")
    open_prs = request_json(
        [f"repos/{repo}/pulls?state=open&base={route['branch']}&per_page=100"]
    )
    if (not isinstance(open_prs, list) or len(open_prs) >= 100
        or sorted(p["number"] for p in open_prs
                  if str(p.get("head", {}).get("ref", "")).startswith(("direct/", "job/")))
        != [args.source_pr]):
        raise DocumentMergeError("one-writer existing-PR identity is not unique")
    check_pr_head_validation(
        canonical_runs(repo, route["workflow"], args.docs_head, "pull_request"),
        route, args.docs_head,
    )
    verify_manual_gate(repo, args.source_pr, args.source_head, args.failed_run_id, args.project)

    workspace = Path(args.workspace).resolve()
    if workspace.exists() or workspace.is_symlink():
        raise DocumentMergeError("recovery workspace must be fresh")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    # Git never executes product code here; use only trusted main code and approved doc bytes.
    git(workspace.parent, "clone", "--no-checkout",
        f"https://github.com/{repo}.git", str(workspace))
    git(workspace, "fetch", "--no-tags", "origin", args.source_head, args.docs_head, args.base)
    git(workspace, "checkout", "--detach", args.source_head)
    if git(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
        raise DocumentMergeError("dirty fresh source checkout")
    original = git(workspace, "show", f"{args.base}:{route['state_path']}")
    if git(workspace, "show", f"{args.source_head}:{route['state_path']}") != original:
        raise DocumentMergeError("source PR modified protected state")
    new_state = git(workspace, "show", f"{args.docs_head}:{route['state_path']}")
    check_scope(original, new_state, args.source_pr, args.source_head, args.failed_run_id)
    for path in paths:
        regular_tree_file(workspace, args.base, path)
        regular_tree_file(workspace, args.source_head, path)
        regular_tree_file(workspace, args.docs_head, path)
        if git(workspace, "show", f"{args.base}:{path}") != git(
            workspace, "show", f"{args.source_head}:{path}"
        ):
            raise DocumentMergeError("source PR document diverged from original base")
        target = workspace / path
        if target.is_symlink() or not target.is_file():
            raise DocumentMergeError("recovery destination is not a regular file")
        # Git output is UTF-8 documentation: preserve exact newline content.
        value = subprocess.run(
            ["git", "-C", str(workspace), "show", f"{args.docs_head}:{path}"],
            capture_output=True, timeout=50, check=False,
        )
        if value.returncode or len(value.stdout) > 400_000:
            raise DocumentMergeError("document blob unavailable or unbounded")
        target.write_bytes(value.stdout)
    if set(git(workspace, "diff", "--name-only").splitlines()) != set(paths):
        raise DocumentMergeError("only the two approved document paths may change")
    git(workspace, "diff", "--check")
    git(workspace, "-c", "core.hooksPath=/dev/null", "add", "--", *paths)
    git(workspace, "-c", "user.name=WONJAE trusted recovery docs",
        "-c", "user.email=trusted-recovery@users.noreply.github.com",
        "-c", "core.hooksPath=/dev/null", "commit", "-m",
        f"docs: attach recovery PR #{args.docs_pr} to source PR #{args.source_pr}")
    attached = check_sha(git(workspace, "rev-parse", "HEAD"), "attached PR head")
    delta = git(workspace, "diff", "--name-only", args.source_head, attached).splitlines()
    if delta != sorted(paths):
        raise DocumentMergeError("trusted attached commit differs from exact document scope")

    # Last-authority check before the *non-force* fast-forward to the same source PR.
    if branch_sha(repo, route["branch"]) != args.base:
        raise DocumentMergeError("active branch moved before attach")
    checked_source_pr(
        request_json([f"repos/{repo}/pulls/{args.source_pr}"]),
        route, args.source_pr, args.source_head, args.base, args.failed_run_id,
    )
    check_pr(
        request_json([f"repos/{repo}/pulls/{args.docs_pr}"]),
        route, args.docs_pr, args.docs_head, args.base,
    )
    git(workspace, "push", "origin", f"HEAD:refs/heads/{source_branch}")
    wait_attached_pr_head(repo, route, args, attached, source_branch)
    # This is validation of a documentation-only PR-head amendment, not a new
    # Direct Worker source test, product integration, or backend deployment.
    for _ in range(72):
        try:
            check_pr_head_validation(
                canonical_runs(repo, route["workflow"], attached, "pull_request"),
                route, attached,
            )
            print(f"RECOVERY_DOCS_ATTACHED source_pr={args.source_pr} "
                  f"docs_pr={args.docs_pr} source_head={attached} ci=PASS "
                  f"resume=OWNER_ACTION_REQUIRED")
            return
        except DocumentMergeError:
            runs = canonical_runs(repo, route["workflow"], attached, "pull_request")
            matching = [run for run in runs.get("workflow_runs", [])
                        if run.get("head_sha") == attached
                        and run.get("event") == "pull_request"
                        and str(run.get("path", "")).split("@", 1)[0] == route["workflow"]
                        and run.get("name") == route["workflow_name"]]
            if matching and max(matching, key=lambda item: item["id"]).get("status") == "completed":
                raise DocumentMergeError("attached PR-head canonical CI failed") from None
        time.sleep(10)
    raise DocumentMergeError("document attachment published; exact PR-head CI not terminal")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-pr", type=int, required=True)
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--docs-pr", type=int, required=True)
    parser.add_argument("--docs-head", required=True)
    parser.add_argument("--failed-run-id", type=int, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--control-sha", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    try:
        attach(args)
    except (DocumentMergeError, yaml.YAMLError) as exc:
        phase = "POST_PUSH_READBACK_DELAY" if str(exc) == "POST_PUSH_READBACK_DELAY" else "GUARD_FAILED"
        print(f"RECOVERY_DOCS_ATTACH_HALTED reason={type(exc).__name__} phase={phase}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
