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

EXACT_WAKE_PATTERN = re.compile(
    r"^\[PRODUCT-WAKE\]\[DISPATCHER-V2\] "
    r"(CLASSMO|WAFL|ESC|MUVEL) "
    r"([A-Z0-9][A-Z0-9-]*-[0-9]{3}) "
    r"([0-9a-f]{40}) ([0-9a-f]{40})$"
)
QUEUE_WAKE_PATTERN = re.compile(
    r"^\[PRODUCT-QUEUE\]\[DISPATCHER-V2\] "
    r"(CLASSMO|WAFL|ESC|MUVEL) "
    r"([A-Z0-9][A-Z0-9-]*-[0-9]{3}) "
    r"([0-9a-f]{40}) ([1-9][0-9]*)$"
)
FIELD_PATTERN = re.compile(r"^- ([A-Za-z0-9_]+): `([^`]*)`\s*$", re.MULTILINE)
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

QUEUE_HEADING = "## Dispatcher v2 source queue evidence"
QUEUE_TERMINAL_HEADING = "## Dispatcher v2 queue terminal evidence"
SOURCE_TERMINAL_HEADING = "## Dispatcher v2 terminal evidence"
QUEUE_STATES = frozenset({"QUEUED", "DISPATCHED"})
TERMINAL_RESULTS = frozenset({"COMPLETED", "FAILED", "MANUAL_REQUIRED", "CANCELLED"})


class QueueContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRegistration:
    project: str
    task_id: str
    control_sha: str
    exact_source_sha: str = ""
    predecessor_issue: int | None = None

    @property
    def mode(self) -> str:
        if self.exact_source_sha:
            return "exact_source_sha"
        return "predecessor_integrated_sha"

    @property
    def workflow_file(self) -> str:
        return WORKFLOW_FILES[self.project]


@dataclass(frozen=True)
class SourceAuthority:
    mode: str
    exact_source_sha: str = ""
    predecessor_issue: int | None = None
    predecessor_task_id: str = ""
    predecessor_control_sha: str = ""
    predecessor_attempt: int | None = None


@dataclass(frozen=True)
class QueueEvidence:
    state: str
    fields: Mapping[str, str]


def parse_registration_title(title: str) -> SourceRegistration | None:
    value = title.strip()
    exact = EXACT_WAKE_PATTERN.fullmatch(value)
    if exact is not None:
        project, task_id, control_sha, source_sha = exact.groups()
        if not task_id.startswith(project + "-"):
            return None
        return SourceRegistration(
            project=project,
            task_id=task_id,
            control_sha=control_sha,
            exact_source_sha=source_sha,
        )

    queued = QUEUE_WAKE_PATTERN.fullmatch(value)
    if queued is None:
        return None
    project, task_id, control_sha, predecessor_issue = queued.groups()
    if not task_id.startswith(project + "-"):
        return None
    return SourceRegistration(
        project=project,
        task_id=task_id,
        control_sha=control_sha,
        predecessor_issue=int(predecessor_issue),
    )


def parse_execution_title(title: str) -> tuple[str, str, str, str] | None:
    match = EXACT_WAKE_PATTERN.fullmatch(title.strip())
    if match is None:
        return None
    project, task_id, control_sha, source_sha = match.groups()
    if not task_id.startswith(project + "-"):
        return None
    return project, task_id, control_sha, source_sha


def execution_title(
    project: str,
    task_id: str,
    control_sha: str,
    source_sha: str,
) -> str:
    return (
        f"[PRODUCT-WAKE][DISPATCHER-V2] {project} {task_id} "
        f"{control_sha} {source_sha}"
    )


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


def latest_queue_terminal_fields(comments: Sequence[str]) -> dict[str, str]:
    for comment in reversed(comments):
        if QUEUE_TERMINAL_HEADING not in comment:
            continue
        fields = parse_fields(comment)
        if fields.get("result") == "COMPLETED":
            return fields
    return {}


def terminal_result(comments: Sequence[str]) -> str:
    for comment in reversed(comments):
        if QUEUE_TERMINAL_HEADING not in comment and SOURCE_TERMINAL_HEADING not in comment:
            continue
        result = parse_fields(comment).get("result", "")
        if result in TERMINAL_RESULTS:
            return result
    return ""


def issue_author(issue: Mapping[str, object]) -> str:
    author = issue.get("author")
    if isinstance(author, Mapping):
        return str(author.get("login") or "")
    user = issue.get("user")
    if isinstance(user, Mapping):
        return str(user.get("login") or "")
    return ""


def project_registrations(
    issues: Iterable[Mapping[str, object]],
    *,
    owner: str,
    project: str,
) -> list[Mapping[str, object]]:
    matches: list[Mapping[str, object]] = []
    for issue in issues:
        registration = parse_registration_title(str(issue.get("title") or ""))
        if registration is None or registration.project != project:
            continue
        if issue_author(issue) != owner:
            continue
        if issue.get("pull_request") is not None:
            continue
        matches.append(issue)
    return sorted(matches, key=lambda item: int(item["number"]))


def queue_state(comments: Sequence[str]) -> str:
    evidence = latest_queue_evidence(comments)
    return evidence.state if evidence is not None else ""


def _require_string(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise QueueContractError(f"control record {key} must be a non-empty string")
    return value.strip()


def source_authority(
    record: Mapping[str, object],
    registration: SourceRegistration,
) -> SourceAuthority:
    if _require_string(record, "task_id") != registration.task_id:
        raise QueueContractError("control record task_id mismatch")
    if _require_string(record, "project") != registration.project:
        raise QueueContractError("control record project mismatch")
    if record.get("operation_type", "source_change") != "source_change":
        raise QueueContractError("queue accepts source_change records only")
    if record.get("integration_authorized") is not True:
        raise QueueContractError("source queue requires integration_authorized true")

    schema_version = record.get("schema_version")
    if schema_version == 1:
        source_sha = _require_string(record, "source_base_sha")
        if SHA_PATTERN.fullmatch(source_sha) is None:
            raise QueueContractError("source_base_sha must be a lowercase 40-character SHA")
        if registration.mode != "exact_source_sha":
            raise QueueContractError("schema v1 source task requires exact PRODUCT-WAKE")
        if registration.exact_source_sha != source_sha:
            raise QueueContractError("PRODUCT-WAKE source SHA does not match control record")
        return SourceAuthority(mode="exact_source_sha", exact_source_sha=source_sha)

    if schema_version != 2:
        raise QueueContractError("source queue supports control schema_version 1 or 2")
    if "source_base_sha" in record:
        raise QueueContractError("schema v2 predecessor-bound task must not set source_base_sha")
    source_base = record.get("source_base")
    if not isinstance(source_base, Mapping):
        raise QueueContractError("schema v2 source_base must be a mapping")
    if source_base.get("mode") != "predecessor_integrated_sha":
        raise QueueContractError("unsupported schema v2 source_base mode")
    predecessor_issue = source_base.get("predecessor_wake_issue")
    predecessor_attempt = source_base.get("predecessor_attempt")
    predecessor_task_id = source_base.get("predecessor_task_id")
    predecessor_control_sha = source_base.get("predecessor_control_sha")
    if not isinstance(predecessor_issue, int) or predecessor_issue < 1:
        raise QueueContractError("predecessor_wake_issue must be a positive integer")
    if not isinstance(predecessor_attempt, int) or predecessor_attempt < 1:
        raise QueueContractError("predecessor_attempt must be a positive integer")
    if not isinstance(predecessor_task_id, str) or not predecessor_task_id:
        raise QueueContractError("predecessor_task_id must be non-empty")
    if not predecessor_task_id.startswith(registration.project + "-"):
        raise QueueContractError("predecessor task namespace mismatch")
    if not isinstance(predecessor_control_sha, str):
        raise QueueContractError("predecessor_control_sha must be a string")
    if SHA_PATTERN.fullmatch(predecessor_control_sha) is None:
        raise QueueContractError("predecessor_control_sha must be a lowercase 40-character SHA")
    if registration.mode != "predecessor_integrated_sha":
        raise QueueContractError("schema v2 source task requires PRODUCT-QUEUE wake")
    if registration.predecessor_issue != predecessor_issue:
        raise QueueContractError("PRODUCT-QUEUE predecessor issue mismatch")
    return SourceAuthority(
        mode="predecessor_integrated_sha",
        predecessor_issue=predecessor_issue,
        predecessor_task_id=predecessor_task_id,
        predecessor_control_sha=predecessor_control_sha,
        predecessor_attempt=predecessor_attempt,
    )


def completion_source_sha(
    comments: Sequence[str],
    *,
    task_id: str,
    control_sha: str,
    attempt: int,
) -> str:
    fields = latest_queue_terminal_fields(comments)
    expected = {
        "task_id": task_id,
        "control_sha": control_sha,
        "attempt": str(attempt),
        "result": "COMPLETED",
    }
    for key, value in expected.items():
        if fields.get(key) != value:
            return ""
    integrated_sha = fields.get("integrated_sha", "")
    if SHA_PATTERN.fullmatch(integrated_sha) is None:
        return ""
    return integrated_sha


def completion_matches_source_run(
    comments: Sequence[str],
    *,
    source_run_id: str,
    source_sha: str,
) -> bool:
    fields = latest_queue_terminal_fields(comments)
    return (
        fields.get("result") == "COMPLETED"
        and fields.get("source_run_id") == source_run_id
        and fields.get("source_sha") == source_sha
    )


def registration_matches_execution(
    registration: SourceRegistration,
    *,
    project: str,
    task_id: str,
    control_sha: str,
) -> bool:
    return (
        registration.project == project
        and registration.task_id == task_id
        and registration.control_sha == control_sha
    )
