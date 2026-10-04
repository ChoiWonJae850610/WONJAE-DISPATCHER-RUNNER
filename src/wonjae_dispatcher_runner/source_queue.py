from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

PROJECTS = ("CLASSMO", "WAFL", "ESC", "MUVEL")
WORKFLOW_FILES = {
    "CLASSMO": "classmo-product-pilot.yml",
    "WAFL": "wafl-product-v2.yml",
    "ESC": "esc-product-v2.yml",
    "MUVEL": "muvel-product-v2.yml",
}
WORKFLOW_NAMES = {project: f"{project} Dispatcher v2" for project in PROJECTS}

WAKE_PATTERN = re.compile(
    r"^\[PRODUCT-WAKE\]\[DISPATCHER-V2\] "
    r"(CLASSMO|WAFL|ESC|MUVEL) "
    r"([A-Z0-9][A-Z0-9-]*-[0-9]{3}) "
    r"([0-9a-f]{40}) ([0-9a-f]{40})$"
)
FIELD_PATTERN = re.compile(r"^- ([A-Za-z0-9_]+): `([^`]*)`\s*$", re.MULTILINE)

QUEUE_HEADING = "## Dispatcher v2 source queue evidence"
QUEUE_TERMINAL_HEADING = "## Dispatcher v2 queue terminal evidence"
SOURCE_TERMINAL_HEADING = "## Dispatcher v2 terminal evidence"
QUEUE_STATES = frozenset({"QUEUED", "DISPATCHED"})
TERMINAL_RESULTS = frozenset({"COMPLETED", "FAILED", "MANUAL_REQUIRED", "CANCELLED"})


@dataclass(frozen=True)
class WakeIdentity:
    project: str
    task_id: str
    control_sha: str
    source_sha: str

    @property
    def workflow_file(self) -> str:
        return WORKFLOW_FILES[self.project]

    @property
    def workflow_name(self) -> str:
        return WORKFLOW_NAMES[self.project]


@dataclass(frozen=True)
class QueueEvidence:
    state: str
    fields: Mapping[str, str]


def parse_wake_title(title: str) -> WakeIdentity | None:
    match = WAKE_PATTERN.fullmatch(title.strip())
    if match is None:
        return None
    project, task_id, control_sha, source_sha = match.groups()
    if not task_id.startswith(project + "-"):
        return None
    return WakeIdentity(project, task_id, control_sha, source_sha)


def parse_fields(markdown: str) -> dict[str, str]:
    return {key: value for key, value in FIELD_PATTERN.findall(markdown)}


def latest_queue_evidence(comments: Sequence[str]) -> QueueEvidence | None:
    for comment in reversed(comments):
        if QUEUE_HEADING not in comment:
            continue
        fields = parse_fields(comment)
        state = fields.get("queue_state", "")
        if state in QUEUE_STATES:
            return QueueEvidence(state, fields)
    return None


def terminal_result(comments: Sequence[str]) -> str:
    for comment in reversed(comments):
        if QUEUE_TERMINAL_HEADING in comment or SOURCE_TERMINAL_HEADING in comment:
            result = parse_fields(comment).get("result", "")
            if result in TERMINAL_RESULTS:
                return result
    return ""


def _issue_author(issue: Mapping[str, object]) -> str:
    author = issue.get("author")
    if isinstance(author, Mapping):
        login = author.get("login")
        return str(login or "")
    user = issue.get("user")
    if isinstance(user, Mapping):
        login = user.get("login")
        return str(login or "")
    return ""


def project_wakes(
    issues: Iterable[Mapping[str, object]],
    *,
    owner: str,
    project: str,
) -> list[Mapping[str, object]]:
    matches: list[Mapping[str, object]] = []
    for issue in issues:
        title = str(issue.get("title") or "")
        identity = parse_wake_title(title)
        if identity is None or identity.project != project:
            continue
        if _issue_author(issue) != owner:
            continue
        if issue.get("pull_request") is not None:
            continue
        matches.append(issue)
    return sorted(matches, key=lambda item: int(item["number"]))


def previous_open_wake(
    current_issue_number: int,
    open_wakes: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    prior = [issue for issue in open_wakes if int(issue["number"]) < current_issue_number]
    return prior[-1] if prior else None


def predecessor_issue_number(comments: Sequence[str]) -> int | None:
    evidence = latest_queue_evidence(comments)
    if evidence is None:
        return None
    value = evidence.fields.get("predecessor_issue", "")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def queue_state(comments: Sequence[str]) -> str:
    evidence = latest_queue_evidence(comments)
    return evidence.state if evidence is not None else ""


def completion_matches_source_run(comments: Sequence[str], source_run_id: str) -> bool:
    for comment in reversed(comments):
        if QUEUE_TERMINAL_HEADING not in comment:
            continue
        fields = parse_fields(comment)
        return (
            fields.get("result") == "COMPLETED"
            and fields.get("source_run_id") == source_run_id
        )
    return False


def can_advance_after_source_run(
    *,
    conclusion: str,
    completed_comments: Sequence[str],
    source_run_id: str,
) -> bool:
    return conclusion == "success" and completion_matches_source_run(
        completed_comments,
        source_run_id,
    )


def candidate_is_dispatchable(
    *,
    candidate_comments: Sequence[str],
    predecessor_comments: Sequence[str],
) -> bool:
    if queue_state(candidate_comments) == "DISPATCHED":
        return False
    predecessor = predecessor_issue_number(candidate_comments)
    if predecessor is None:
        return False
    return terminal_result(predecessor_comments) == "COMPLETED"
