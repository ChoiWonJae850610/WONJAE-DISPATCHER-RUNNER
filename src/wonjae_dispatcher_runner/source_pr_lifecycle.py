from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Protocol

TERMINAL_RESULTS = frozenset({"FAILED", "MANUAL_REQUIRED", "CANCELLED"})
FINAL_RECOVERY_STATES = frozenset({"FINAL", "FINAL_GUARD"})
MARKDOWN_FIELD = re.compile(r"^- ([A-Za-z0-9_]+): \`([^\`]*)\`\\s*$", re.MULTILINE)


class LifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceIdentity:
    project: str
    repository: str
    target_branch: str
    task_id: str
    attempt: int
    control_sha: str
    source_sha: str
    runner_run_id: str

    @property
    def expected_branch(self) -> str:
        suffix = "" if self.attempt == 1 else f"-a{self.attempt}"
        return f"job/{self.task_id}{suffix}"

    @property
    def wake_title(self) -> str:
        return (
            f"[PRODUCT-WAKE][DISPATCHER-V2] {self.project} {self.task_id} "
            f"{self.control_sha} {self.source_sha}"
        )


@dataclass(frozen=True)
class TerminalEvidence:
    fields: dict[str, str]

    @property
    def result(self) -> str:
        return self.fields.get("result", "")

    @property
    def recovery_state(self) -> str:
        return self.fields.get("recovery_state", "")


@dataclass(frozen=True)
class CleanupResult:
    status: str
    pr_number: int | None
    detail: str
    next_action: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "pr_number": self.pr_number,
            "detail": self.detail,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class Conflict:
    pr_number: int
    head_ref: str
    task_id: str
    attempt: str
    kind: str
    terminal_result: str = ""
    wake_issue_number: int | None = None


class GitHubClient(Protocol):
    def get_pr(self, repository: str, number: int) -> dict[str, object]: ...

    def list_open_job_prs(self, repository: str, target_branch: str) -> list[dict[str, object]]:
        ...

    def close_pr(self, repository: str, number: int) -> None: ...

    def find_terminal_evidence(
        self, runner_repository: str, wake_title: str
    ) -> tuple[int, str] | None: ...


def parse_fields(markdown: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in MARKDOWN_FIELD.finditer(markdown)}


def parse_terminal_evidence(markdown: str) -> TerminalEvidence:
    if "## Dispatcher v2 terminal evidence" not in markdown:
        raise LifecycleError("terminal evidence heading is missing")
    fields = parse_fields(markdown)
    return TerminalEvidence(fields)


def parse_pr_metadata(body: str) -> dict[str, str]:
    if "## Dispatcher v2 metadata" not in body:
        return {}
    return parse_fields(body)


def _identity_errors(pr: dict[str, object], identity: SourceIdentity) -> list[str]:
    errors: list[str] = []
    metadata = parse_pr_metadata(str(pr.get("body") or ""))
    expected = {
        "project": identity.project,
        "task_id": identity.task_id,
        "attempt": str(identity.attempt),
        "control_sha": identity.control_sha,
        "repository": identity.repository,
        "target_branch": identity.target_branch,
        "source_base_sha": identity.source_sha,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            errors.append(f"metadata {key} mismatch")
    if str(pr.get("baseRefName") or "") != identity.target_branch:
        errors.append("base branch mismatch")
    if str(pr.get("headRefName") or "") != identity.expected_branch:
        errors.append("head branch mismatch")
    if pr.get("mergedAt"):
        errors.append("pull request is already merged")
    return errors


def _evidence_errors(
    evidence: TerminalEvidence,
    identity: SourceIdentity,
    terminal_status: str,
    pr_number: int | None,
) -> list[str]:
    errors: list[str] = []
    expected = {
        "project": identity.project,
        "task_id": identity.task_id,
        "attempt": str(identity.attempt),
        "control_sha": identity.control_sha,
        "source_sha": identity.source_sha,
        "runner_run_id": identity.runner_run_id,
        "result": terminal_status,
    }
    for key, value in expected.items():
        if evidence.fields.get(key) != value:
            errors.append(f"terminal evidence {key} mismatch")
    if terminal_status not in TERMINAL_RESULTS:
        errors.append("result is not a source terminal status")
    if evidence.recovery_state not in FINAL_RECOVERY_STATES:
        errors.append("bounded recovery is not final")
    if pr_number is not None and evidence.fields.get("product_pr"):
        if evidence.fields["product_pr"] != str(pr_number):
            errors.append("terminal evidence product_pr mismatch")
    return errors


def _matching_open_prs(
    client: GitHubClient,
    identity: SourceIdentity,
) -> list[dict[str, object]]:
    matches = []
    for pr in client.list_open_job_prs(identity.repository, identity.target_branch):
        if str(pr.get("headRefName") or "") != identity.expected_branch:
            continue
        if not _identity_errors(pr, identity):
            matches.append(pr)
    return matches


def terminalize_exact_pr(
    client: GitHubClient,
    identity: SourceIdentity,
    evidence_markdown: str,
    terminal_status: str,
    pr_number: int | None = None,
) -> CleanupResult:
    try:
        evidence = parse_terminal_evidence(evidence_markdown)
    except LifecycleError as exc:
        return CleanupResult(
            "RESIDUE",
            pr_number,
            str(exc),
            "verify exact terminal evidence before closing the Product PR",
        )

    evidence_errors = _evidence_errors(evidence, identity, terminal_status, pr_number)
    if evidence_errors:
        return CleanupResult(
            "RESIDUE",
            pr_number,
            "; ".join(evidence_errors),
            "verify exact terminal identity and bounded recovery before Product PR cleanup",
        )

    try:
        if pr_number is not None:
            pr = client.get_pr(identity.repository, pr_number)
        else:
            candidates = _matching_open_prs(client, identity)
            if not candidates:
                return CleanupResult(
                    "NOT_APPLICABLE",
                    None,
                    "no exact open Product PR exists for this terminal attempt",
                    "inspect the exact runner evidence before retry",
                )
            if len(candidates) != 1:
                return CleanupResult(
                    "RESIDUE",
                    None,
                    "multiple exact open Product PR candidates were found",
                    "resolve duplicate exact Product PRs before starting another source task",
                )
            pr = candidates[0]
            pr_number = int(pr["number"])
    except Exception as exc:  # noqa: BLE001 - cleanup residue must preserve terminal result
        return CleanupResult(
            "RESIDUE",
            pr_number,
            f"Product PR lookup failed: {exc}",
            "verify the exact terminal Product PR and close it without merge if still open",
        )

    errors = _identity_errors(pr, identity)
    if errors:
        return CleanupResult(
            "RESIDUE",
            pr_number,
            "; ".join(errors),
            "verify the exact Product PR identity; no PR was closed",
        )

    state = str(pr.get("state") or "").upper()
    if state == "CLOSED":
        return CleanupResult(
            "ALREADY_CLOSED",
            pr_number,
            "exact Product PR is already closed and unmerged",
            "inspect the exact terminal evidence before retry",
        )
    if state != "OPEN":
        return CleanupResult(
            "RESIDUE",
            pr_number,
            f"unexpected Product PR state {state or 'UNKNOWN'}",
            "inspect and close only the exact terminal Product PR",
        )

    try:
        client.close_pr(identity.repository, pr_number)
        readback = client.get_pr(identity.repository, pr_number)
    except Exception as exc:  # noqa: BLE001 - cleanup residue must not change terminal result
        return CleanupResult(
            "RESIDUE",
            pr_number,
            f"Product PR close failed: {exc}",
            f"close exact terminal Product PR #{pr_number} without merging or deleting its branch",
        )

    readback_errors = _identity_errors(readback, identity)
    if readback_errors:
        return CleanupResult(
            "RESIDUE",
            pr_number,
            "; ".join(readback_errors),
            f"verify exact terminal Product PR #{pr_number} after close attempt",
        )
    if str(readback.get("state") or "").upper() != "CLOSED" or readback.get("mergedAt"):
        return CleanupResult(
            "RESIDUE",
            pr_number,
            "Product PR close readback did not prove CLOSED and unmerged",
            f"close exact terminal Product PR #{pr_number} without merging or deleting its branch",
        )
    return CleanupResult(
        "CLOSED",
        pr_number,
        "exact terminal Product PR closed; branch, commits, checks, and conversation preserved",
        "inspect the exact terminal evidence before retry",
    )


def _terminal_evidence_matches(
    markdown: str,
    metadata: dict[str, str],
) -> tuple[bool, str]:
    try:
        evidence = parse_terminal_evidence(markdown)
    except LifecycleError:
        return False, ""
    result = evidence.result
    if result not in TERMINAL_RESULTS:
        return False, result
    expected = {
        "task_id": metadata.get("task_id", ""),
        "attempt": metadata.get("attempt", ""),
        "control_sha": metadata.get("control_sha", ""),
        "source_sha": metadata.get("source_base_sha", ""),
    }
    for key, value in expected.items():
        if not value or evidence.fields.get(key) != value:
            return False, result
    return True, result


def diagnose_open_job_prs(
    client: GitHubClient,
    project: str,
    repository: str,
    target_branch: str,
    runner_repository: str,
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for pr in client.list_open_job_prs(repository, target_branch):
        head_ref = str(pr.get("headRefName") or "")
        if not head_ref.startswith("job/"):
            continue
        metadata = parse_pr_metadata(str(pr.get("body") or ""))
        task_id = metadata.get("task_id", "")
        attempt = metadata.get("attempt", "")
        kind = "ACTIVE_OR_UNRESOLVED"
        terminal_result = ""
        wake_issue_number = None

        required = (
            metadata.get("project") == project
            and metadata.get("repository") == repository
            and metadata.get("target_branch") == target_branch
            and bool(task_id)
            and bool(attempt)
            and bool(metadata.get("control_sha"))
            and bool(metadata.get("source_base_sha"))
        )
        if required:
            wake_title = (
                f"[PRODUCT-WAKE][DISPATCHER-V2] {project} {task_id} "
                f"{metadata['control_sha']} {metadata['source_base_sha']}"
            )
            terminal = client.find_terminal_evidence(runner_repository, wake_title)
            if terminal is not None:
                issue_number, body = terminal
                exact, result = _terminal_evidence_matches(body, metadata)
                if exact:
                    kind = "STALE_TERMINAL_OPEN_PR"
                    terminal_result = result
                    wake_issue_number = issue_number

        conflicts.append(
            Conflict(
                pr_number=int(pr["number"]),
                head_ref=head_ref,
                task_id=task_id,
                attempt=attempt,
                kind=kind,
                terminal_result=terminal_result,
                wake_issue_number=wake_issue_number,
            )
        )
    return conflicts


class GhClient:
    def __init__(self, product_token: str, runner_token: str = "") -> None:
        if not product_token:
            raise LifecycleError("PRODUCT_GH_TOKEN is missing")
        self.product_token = product_token
        self.runner_token = runner_token or product_token

    @staticmethod
    def _run(args: list[str], token: str) -> str:
        env = os.environ.copy()
        env["GH_TOKEN"] = token
        completed = subprocess.run(
            ["gh", *args],
            check=True,
            text=True,
            capture_output=True,
            env=env,
        )
        return completed.stdout

    def get_pr(self, repository: str, number: int) -> dict[str, object]:
        raw = self._run(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repository,
                "--json",
                "number,url,state,isDraft,mergedAt,baseRefName,headRefName,headRefOid,body",
            ],
            self.product_token,
        )
        return json.loads(raw)

    def list_open_job_prs(
        self, repository: str, target_branch: str
    ) -> list[dict[str, object]]:
        raw = self._run(
            [
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "open",
                "--base",
                target_branch,
                "--limit",
                "100",
                "--json",
                "number,url,state,isDraft,mergedAt,baseRefName,headRefName,headRefOid,body",
            ],
            self.product_token,
        )
        return [
            item
            for item in json.loads(raw)
            if str(item.get("headRefName") or "").startswith("job/")
        ]

    def close_pr(self, repository: str, number: int) -> None:
        self._run(
            [
                "api",
                "--method",
                "PATCH",
                f"repos/{repository}/pulls/{number}",
                "-f",
                "state=closed",
            ],
            self.product_token,
        )

    def find_terminal_evidence(
        self, runner_repository: str, wake_title: str
    ) -> tuple[int, str] | None:
        query = f'repo:{runner_repository} is:issue in:title "{wake_title}"'
        raw = self._run(
            ["api", "-X", "GET", "search/issues", "-f", f"q={query}", "-f", "per_page=100"],
            self.runner_token,
        )
        items = json.loads(raw).get("items", [])
        exact = [item for item in items if item.get("title") == wake_title]
        if len(exact) != 1:
            return None
        issue_number = int(exact[0]["number"])
        comments_raw = self._run(
            [
                "api",
                f"repos/{runner_repository}/issues/{issue_number}/comments?per_page=100",
            ],
            self.runner_token,
        )
        comments = json.loads(comments_raw)
        for comment in reversed(comments):
            body = str(comment.get("body") or "")
            if "## Dispatcher v2 terminal evidence" in body:
                return issue_number, body
        return None
