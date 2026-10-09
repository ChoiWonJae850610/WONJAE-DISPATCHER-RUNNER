#!/usr/bin/env python3
"""Exact-SHA, documentation-only product PR integration after SANJINWORKS Owner action.

This script runs only in a trusted Runner workflow dispatched by the authenticated
SANJINWORKS Owner UI. It neither creates product source changes nor invokes
provider services, builds, migrations, releases or credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

SHA = re.compile(r"^[0-9a-f]{40}$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9_-]*$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")
ALLOWED_STATUSES = frozenset(("added", "modified"))


class DocumentMergeError(RuntimeError):
    """A failed check must never grant product integration authority."""


def check_sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise DocumentMergeError(f"invalid {name}")
    return value


def allowed_document_path(path: object, state_path: str) -> bool:
    if not isinstance(path, str) or not path or len(path) > 512:
        return False
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    return path == state_path or (path.startswith("docs/") and len(parts) >= 2)


def route_from_registry(registry: object, project: str) -> dict[str, str]:
    if not PROJECT.fullmatch(project) or project == "KDN":
        raise DocumentMergeError("project is outside the registered product scope")
    if not isinstance(registry, dict):
        raise DocumentMergeError("invalid DEV-CONTROL registry")
    projects = registry.get("projects")
    config = projects.get(project) if isinstance(projects, dict) else None
    if not isinstance(config, dict) or config.get("status") != "active":
        raise DocumentMergeError("project is not registered as active")
    execution = config.get("execution")
    ci = config.get("ci")
    validation = ci.get("validation") if isinstance(ci, dict) else None
    if not isinstance(execution, dict) or execution.get("mode") != "direct_worker":
        raise DocumentMergeError("product is not on the registered Direct Worker route")
    values = {
        "repo": config.get("repository"),
        "branch": config.get("branch"),
        "workflow": validation.get("path") if isinstance(validation, dict) else None,
        "workflow_name": validation.get("name") if isinstance(validation, dict) else None,
        "state_path": execution.get("state_path"),
    }
    if not isinstance(values["repo"], str) or not REPO.fullmatch(values["repo"]):
        raise DocumentMergeError("invalid registered repository")
    if not isinstance(values["branch"], str) or not BRANCH.fullmatch(values["branch"]):
        raise DocumentMergeError("invalid active development branch")
    if values["branch"] in ("main", "master"):
        raise DocumentMergeError("documentation merge cannot target main or master")
    if not isinstance(values["workflow"], str) or not re.fullmatch(
        r"\.github/workflows/[A-Za-z0-9_-]+\.ya?ml", values["workflow"]
    ):
        raise DocumentMergeError("invalid canonical validation workflow")
    if not isinstance(values["workflow_name"], str) or not values["workflow_name"]:
        raise DocumentMergeError("canonical workflow name is missing")
    if values["state_path"] != ".wonjae/execution-state.yaml":
        raise DocumentMergeError("unrecognized product-owned execution state path")
    return values


def check_pr(pr: object, route: dict[str, str], pr_number: int,
             head: str, base: str) -> None:
    if not isinstance(pr, dict) or pr.get("number") != pr_number:
        raise DocumentMergeError("PR number does not match the Owner selection")
    pr_base = pr.get("base")
    pr_head = pr.get("head")
    if pr.get("state") != "open" or pr.get("merged_at") or pr.get("draft") is not False:
        raise DocumentMergeError("PR must be open, unmerged and not draft")
    if not isinstance(pr_base, dict) or not isinstance(pr_head, dict):
        raise DocumentMergeError("PR base/head metadata is unavailable")
    if pr_base.get("ref") != route["branch"] or pr_base.get("sha") != base:
        raise DocumentMergeError("PR base branch or base SHA changed")
    if pr_head.get("sha") != head:
        raise DocumentMergeError("PR head SHA changed")
    if str(pr_head.get("ref", "")).startswith(("direct/", "job/")):
        raise DocumentMergeError("source-worker PR may not use documentation merge")
    if pr_base.get("repo", {}).get("full_name") != route["repo"]:
        raise DocumentMergeError("PR base repository mismatch")
    if pr_head.get("repo", {}).get("full_name") != route["repo"]:
        raise DocumentMergeError("cross-repository PR cannot use document merge")
    if pr.get("user", {}).get("login") not in ("ChoiWonJae850610", "github-actions[bot]"):
        raise DocumentMergeError("PR author is outside trusted document preparation actors")
    if pr.get("mergeable") is not True:
        raise DocumentMergeError("PR mergeability has not been proven")
    if not isinstance(pr.get("changed_files"), int) or not 1 <= pr["changed_files"] <= 100:
        raise DocumentMergeError("document PR file inventory exceeds trusted bounds")


def check_files(files: object, count: int, state_path: str) -> None:
    if not isinstance(files, list) or len(files) != count:
        raise DocumentMergeError("incomplete PR changed-file inventory")
    for file in files:
        if not isinstance(file, dict):
            raise DocumentMergeError("malformed PR file")
        path = file.get("filename")
        if file.get("status") not in ALLOWED_STATUSES or file.get("previous_filename"):
            raise DocumentMergeError("deleted, renamed or unknown PR file mutation")
        if not allowed_document_path(path, state_path):
            raise DocumentMergeError("non-document source or protected path in PR")
        if file.get("changes", 0) > 40000:
            raise DocumentMergeError("document file change exceeds bounded review scope")


def check_pr_head_validation(payload: object, route: dict[str, str], head: str) -> int:
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise DocumentMergeError("canonical PR-head Actions runs cannot be read")
    matched = sorted((
        run for run in runs
        if isinstance(run, dict)
        and run.get("head_sha") == head
        and run.get("event") == "pull_request"
        and str(run.get("path", "")).split("@", 1)[0] == route["workflow"]
        and run.get("name") == route["workflow_name"]
    ), key=lambda run: run.get("id", 0), reverse=True)
    if not matched or matched[0].get("status") != "completed" or (
        matched[0].get("conclusion") != "success"
    ):
        raise DocumentMergeError("latest exact PR-head canonical CI is not PASS")
    return int(matched[0]["id"])


def check_integrated_commit(payload: object, base: str, head: str, integrated: str) -> None:
    if not isinstance(payload, dict) or payload.get("sha") != integrated:
        raise DocumentMergeError("integrated commit SHA mismatch")
    parents = payload.get("parents")
    if not isinstance(parents, list) or len(parents) != 2 or (
        parents[0].get("sha") != base or parents[1].get("sha") != head
    ):
        raise DocumentMergeError("integrated merge ancestry differs from validated head/base")


def request_json(arguments: list[str]) -> object:
    result = subprocess.run(
        ["gh", "api", *arguments],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode != 0:
        # Do not expose upstream response bodies, URLs, signed links or secrets.
        raise DocumentMergeError("GitHub exact-identity read or merge request failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DocumentMergeError("GitHub returned malformed JSON") from exc


def branch_sha(repo: str, branch: str) -> str:
    payload = request_json([f"repos/{repo}/branches/{branch}"])
    return check_sha(payload["commit"]["sha"], "current product HEAD")


def canonical_runs(repo: str, workflow: str, sha: str, event: str) -> object:
    workflow_file = workflow.rsplit("/", 1)[-1]
    return request_json([
        f"repos/{repo}/actions/workflows/{workflow_file}/runs"
        f"?head_sha={sha}&event={event}&per_page=100"
    ])


def integrate(args: argparse.Namespace) -> int:
    for label in ("head", "base", "control_sha"):
        check_sha(getattr(args, label), label)
    if args.pr_number < 1 or args.pr_number > 100000:
        raise DocumentMergeError("PR number exceeds trusted bound")
    if not os.environ.get("GH_TOKEN"):
        raise DocumentMergeError("project-scoped integration token is not configured")

    registry = yaml.safe_load(Path(args.registry).read_text(encoding="utf-8"))
    route = route_from_registry(registry, args.project)
    repo = route["repo"]
    if branch_sha(repo, route["branch"]) != args.base:
        raise DocumentMergeError("product active branch moved after Owner review")
    pr = request_json([f"repos/{repo}/pulls/{args.pr_number}"])
    check_pr(pr, route, args.pr_number, args.head, args.base)
    files = request_json([f"repos/{repo}/pulls/{args.pr_number}/files?per_page=100"])
    check_files(files, pr["changed_files"], route["state_path"])
    open_prs = request_json([
        f"repos/{repo}/pulls?state=open&base={route['branch']}&per_page=100"
    ])
    if not isinstance(open_prs, list) or len(open_prs) >= 100:
        raise DocumentMergeError("open PR inventory is ambiguous")
    if any(
        isinstance(other, dict)
        and other.get("number") != args.pr_number
        and str(other.get("head", {}).get("ref", "")).startswith(("direct/", "job/"))
        for other in open_prs
    ):
        raise DocumentMergeError("source-writing PR is open; document merge blocked")
    pr_run = check_pr_head_validation(
        canonical_runs(repo, route["workflow"], args.head, "pull_request"),
        route, args.head,
    )

    # Read back the exact base and PR head immediately before the one guarded
    # GitHub merge. An Owner-button dispatch is not blanket product authority.
    if branch_sha(repo, route["branch"]) != args.base:
        raise DocumentMergeError("base branch moved before merge")
    check_pr(
        request_json([f"repos/{repo}/pulls/{args.pr_number}"]),
        route, args.pr_number, args.head, args.base,
    )
    merged = request_json([
        "--method", "PUT", f"repos/{repo}/pulls/{args.pr_number}/merge",
        "-f", "merge_method=merge", "-f", f"sha={args.head}",
    ])
    if not isinstance(merged, dict) or merged.get("merged") is not True:
        raise DocumentMergeError("GitHub did not confirm the guarded PR merge")
    integrated = check_sha(merged.get("sha"), "integrated SHA")
    check_integrated_commit(
        request_json([f"repos/{repo}/commits/{integrated}"]),
        args.base, args.head, integrated,
    )
    if branch_sha(repo, route["branch"]) != integrated:
        raise DocumentMergeError("integrated SHA is no longer the active branch HEAD")

    integrated_run = None
    # The push validation is triggered by GitHub integration, not by ChatGPT
    # or by a new product source task. A pending CI is never reported as PASS.
    for _ in range(72):
        runs = canonical_runs(repo, route["workflow"], integrated, "push")
        eligible = sorted((
            run for run in runs.get("workflow_runs", [])
            if run.get("event") == "push" and run.get("head_sha") == integrated
            and str(run.get("path", "")).split("@", 1)[0] == route["workflow"]
            and run.get("name") == route["workflow_name"]
        ), key=lambda run: run["id"], reverse=True)
        if eligible:
            current = eligible[0]
            if current.get("status") == "completed":
                if current.get("conclusion") != "success":
                    raise DocumentMergeError("integrated canonical CI finished non-successfully")
                integrated_run = int(current["id"])
                break
        time.sleep(10)
    if integrated_run is None:
        raise DocumentMergeError("PR merged but integrated exact-SHA CI remains unverified")

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(
                f"## {args.project} document-only merge\n\n"
                f"- Product PR: #{args.pr_number}\n"
                f"- Exact PR head: {args.head}\n"
                f"- Exact base: {args.base}\n"
                f"- PR-head validation run: {pr_run} PASS\n"
                f"- Integrated SHA: {integrated}\n"
                f"- Integrated validation run: {integrated_run} PASS\n"
                "- Source writer / EAS / OTA / Production / credentials / devices: NOT_RUN\n"
            )
    print(f"DOCUMENT_MERGE_PASS product={args.project} pr={args.pr_number} sha={integrated}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--control-sha", required=True)
    args = parser.parse_args()
    try:
        return integrate(args)
    except (DocumentMergeError, KeyError, TypeError, IndexError, ValueError) as exc:
        print(f"::error::document-only PR integration blocked: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
