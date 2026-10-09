"""Deterministic, non-executable product recovery documentation for exact initial-turn timeouts.

This module NEVER calls GitHub, Codex, a provider or a source writer. A separately
trusted admission/publisher must verify the failed run and protected checkout.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

import yaml

from .execution_state import ExecutionStateError

SHA = re.compile(r"^[0-9a-f]{40}$")
TASK = re.compile(r"^[A-Z][A-Z0-9-]{2,100}$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9-]{1,30}$")
INITIAL = re.compile(
    r"(?m)^.{0,160}\bINITIAL_TURN attempt=([1-3]) "
    r"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source=([0-9a-f]{40})\r?$"
)
TERMINAL = "INITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0"
MAX_LOG_CHARS = 2_000_000


class RecoveryPreparationError(RuntimeError):
    """Ambiguous or unverified evidence must not become a docs PR."""


@dataclass(frozen=True)
class TimeoutEvidence:
    project: str
    repository: str
    branch: str
    source_sha: str
    runner_run_id: int
    task_id: str
    error_code: str = "INITIAL_CODEX_TIMEOUT_EXHAUSTED"
    attempts: int = 3


def parse_initial_turn_timeout(log: str, exact_source_sha: str) -> bool:
    """Extract only exact known markers; raw logs must never enter PRs/UI."""
    if not SHA.fullmatch(exact_source_sha) or not isinstance(log, str):
        return False
    if not 0 < len(log) <= MAX_LOG_CHARS or TERMINAL not in log:
        return False
    observations = INITIAL.findall(log)
    return observations == [("1", exact_source_sha), ("2", exact_source_sha),
                            ("3", exact_source_sha)]


def check_evidence(evidence: TimeoutEvidence, state: dict) -> None:
    if not PROJECT.fullmatch(evidence.project) or not TASK.fullmatch(evidence.task_id):
        raise RecoveryPreparationError("untrusted project or task identity")
    if not SHA.fullmatch(evidence.source_sha) or not 0 < evidence.runner_run_id < 10**18:
        raise RecoveryPreparationError("untrusted source SHA or run identity")
    if not re.fullmatch(r"[A-Za-z0-9-]{2,39}/[A-Z][A-Z0-9-]{1,30}", evidence.repository):
        raise RecoveryPreparationError("project repository is not a safe registered locator")
    if evidence.repository.split("/", 1)[1] != evidence.project:
        raise RecoveryPreparationError("product repository identity mismatch")
    if evidence.branch in ("main", "master") or not re.fullmatch(
        r"[a-z0-9][a-z0-9/_-]{1,90}", evidence.branch
    ):
        raise RecoveryPreparationError("unexpected active branch")
    if evidence.error_code != "INITIAL_CODEX_TIMEOUT_EXHAUSTED" or evidence.attempts != 3:
        raise RecoveryPreparationError("not an exact 3-turn timeout")
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise RecoveryPreparationError("execution-state schema unknown")
    if state.get("project") != evidence.project:
        raise RecoveryPreparationError("execution-state project mismatch")
    current = state.get("next_action")
    if not isinstance(current, dict) or current.get("type") != "SOURCE_READY":
        raise RecoveryPreparationError("source stage not current")
    if current.get("source_task_id") != evidence.task_id:
        raise RecoveryPreparationError("failed task is not current")
    if not isinstance(current.get("source_scope"), list) or not current["source_scope"]:
        raise RecoveryPreparationError("missing approved source scope")
    if state.get("after_source_success") is None:
        raise RecoveryPreparationError("missing recorded after-source boundary")
    queue = state.get("source_success_queue", [])
    if not isinstance(queue, list) or len(queue) > 8:
        raise RecoveryPreparationError("unbounded successor queue")


def recovery_paths(evidence: TimeoutEvidence) -> tuple[str, str, str]:
    # Branch is a deterministic idempotency fence; never an arbitrary PR target.
    return (
        f"docs/recovery-{evidence.project.lower()}-{evidence.runner_run_id}",
        f"docs/operations/RECOVERY-{evidence.task_id}-{evidence.runner_run_id}.md",
        ".wonjae/execution-state.yaml",
    )


def render_recovery(
    evidence: TimeoutEvidence, original_state: dict
) -> tuple[str, str]:
    """Produce exactly two docs/state file bodies; both remain non-executable."""
    check_evidence(evidence, original_state)
    original = original_state["next_action"]
    successor = original_state["after_source_success"]
    tail = original_state.get("source_success_queue", [])
    next_titles = [str(successor.get("title", "unspecified"))]
    next_titles += [str(x.get("title", "unspecified")) for x in tail]
    next_titles = [re.sub(r"[\r\n|\x00-\x1f]", " ", x)[:130] for x in next_titles]
    stage_title = re.sub(r"[\r\n|\x00-\x1f]", " ", str(original.get("title", "")))[:150]
    # Only trusted enumerated values are reflected; source_scope freeform text is
    # intentionally NOT included, so credentials embedded in prose never escape.
    doc = (
        f"# Failed source execution recovery — {evidence.task_id}\n\n"
        f"Status: **PREPARED / NOT SOURCE_READY / NOT RETRIED**.\n\n"
        f"- Project: `{evidence.project}`\n"
        f"- Registered active branch: `{evidence.branch}`\n"
        f"- Original exact source SHA: `{evidence.source_sha}`\n"
        f"- Owner-started source run: `{evidence.runner_run_id}`\n"
        f"- Current approved stage: {stage_title}\n"
        f"- Failure code: `{evidence.error_code}` (three 480-second initial turns, commits 0).\n"
        f"- Source PR: **NOT CREATED**. Product source/Provider/EAS/device: **NOT_RUN**.\n\n"
        "## Evidence and uncertainty\n\n"
        "The trusted run's exact failed job/step and three bounded initial-turn "
        "markers were checked. This proves the initial source turn exhausted its "
        "deadline, **not** whether prompt size, service transport, authentication, "
        "resource limits or a code defect caused it. No previous committed source "
        "changes are accepted. Avoid blind reruns or increasing the timeout.\n\n"
        "## Bounded repair phases\n\n"
        "1. **Common Runner readiness:** perform a non-secret read-only check for "
        "Codex session availability, first-turn transport and Linux resource "
        "conditions. The preflight must not dispatch product source work or "
        "expose tokens. If unavailable, classify as Runner recovery instead "
        "of continuing product development.\n"
        "2. **Product replan:** after readiness is proven, prepare a separate "
        "docs-only PR splitting the previously approved source stage into "
        "the smallest independently testable substeps. Preserve its original "
        "functional scope, allowed paths, validation contract and subsequent "
        "approval/QA stop gates. No new feature is authorized here.\n"
        "3. **Owner integration:** exact PR-head validation, authenticated "
        "SANJINWORKS document-PR merge, and integrated SHA validation are "
        "needed before the corrected SOURCE_READY stage can be shown. A separate "
        "Owner click still initiates any source work.\n\n"
        "## Preserved follow-on intent (not automatic permissions)\n\n"
        + "\n".join(f"- {title}" for title in next_titles)
        + "\n\n"
        "Do not delete or mutate old data, EAS/OTA, Cloudflare, credentials, "
        "permissions, production, or unfinished physical QA.\n"
    )
    current = copy.deepcopy(original_state)
    current["next_action"] = {
        "type": "DECISION_REQUIRED",
        "title": f"실패 복구 진단: {evidence.task_id}",
        "source_task_id": None,
        "owner_action": (
            "기존 Direct Worker의 초기 Codex 480초 시간초과가 3회 발생했습니다. "
            "개발 재시작 전 공통 Runner/Codex 연결·인증·자원 점검이 필요합니다. "
            f"정확한 실행 #{evidence.runner_run_id}과 해당 복구 문서를 확인하세요. "
            "점검 PASS 및 기존 범위의 작은 개발 단계가 문서 PR로 등록되기 전에는 "
            "다음 작업을 시작하지 않습니다."
        ),
        "source_scope": [],
    }
    current["after_source_success"] = None
    current["source_success_queue"] = []
    try:
        text = yaml.safe_dump(current, allow_unicode=True, sort_keys=False)
    except yaml.YAMLError as exc:
        raise RecoveryPreparationError("cannot serialize protected execution-state") from exc
    if len(doc) > 10_000 or len(text) > 14_000:
        raise RecoveryPreparationError("recovery documents exceed strict limits")
    return doc, text
