"""Deterministic, non-executable product recovery documentation for exact initial-turn timeouts.

This module NEVER calls GitHub, Codex, a provider or a source writer. A separately
trusted admission/publisher must verify the failed run and protected checkout.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

import yaml

SHA = re.compile(r"^[0-9a-f]{40}$")
TASK = re.compile(r"^[A-Z][A-Z0-9-]{2,100}$")
PROJECT = re.compile(r"^[A-Z][A-Z0-9-]{1,30}$")
INITIAL = re.compile(
    r"(?m)^.{0,512}\bINITIAL_TURN attempt=([1-3]) "
    r"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source=([0-9a-f]{40})\r?$"
)
TERMINAL = "INITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0"
TIMEOUT_CODE = "INITIAL_CODEX_TIMEOUT_EXHAUSTED"
MIXED_CAPACITY_CODE = "INITIAL_CODEX_TIMEOUT_THEN_MODEL_CAPACITY"
MIXED_INITIAL = re.compile(
    r"(?m)^.{0,512}\bINITIAL_TURN attempt=([1-3]) "
    r"outcome=(INITIAL_CODEX_TIMEOUT_480_SECONDS|FAILED_NOT_RETRYABLE) "
    r"source=([0-9a-f]{40})\r?$"
)
MIXED_TIMEOUT_PHASE = re.compile(
    r"(?m)^.{0,512}\bDIRECT_CODEX_TIMEOUT_PHASE phase=turn_run elapsed_s=480\r?$"
)
MIXED_TIMEOUT_LINE = re.compile(
    r"(?m)^.{0,512}\bINITIAL_CODEX_TIMEOUT_480_SECONDS\r?$"
)
MIXED_CAPACITY_LINE = re.compile(
    r"(?m)^.{0,512}\bRuntimeError: "
    r"Selected model is at capacity\. Please try a different model\.\r?$"
)
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
    # GitHub log endpoints and CLI may prepend long job labels or ANSI styles.
    # Normalize presentation only; keep strict SHA, three ordered attempts and
    # the original zero-commit timeout terminal. Reject missing/extra markers.
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", log)
    observations = INITIAL.findall(cleaned)
    return observations == [("1", exact_source_sha), ("2", exact_source_sha),
                            ("3", exact_source_sha)]


def parse_initial_turn_mixed_capacity(log: str, exact_source_sha: str) -> bool:
    """Admit only two exact turn_run timeouts then an SDK model-capacity failure.

    The verified, Owner-started GitHub job/step and unchanged product HEAD are
    independently required by the trusted publisher. This classification only
    authorizes a docs/state PR, never a replacement source turn.
    """
    if not SHA.fullmatch(exact_source_sha) or not isinstance(log, str):
        return False
    if not 0 < len(log) <= MAX_LOG_CHARS or TERMINAL in log:
        return False
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", log)
    markers = list(MIXED_INITIAL.finditer(cleaned))
    if len(markers) != 3 or cleaned.count("INITIAL_TURN attempt=") != 3:
        return False
    expected = [
        ("1", "INITIAL_CODEX_TIMEOUT_480_SECONDS", exact_source_sha),
        ("2", "INITIAL_CODEX_TIMEOUT_480_SECONDS", exact_source_sha),
        ("3", "FAILED_NOT_RETRYABLE", exact_source_sha),
    ]
    if [match.groups() for match in markers] != expected:
        return False
    phases = list(MIXED_TIMEOUT_PHASE.finditer(cleaned))
    timeouts = list(MIXED_TIMEOUT_LINE.finditer(cleaned))
    capacities = list(MIXED_CAPACITY_LINE.finditer(cleaned))
    if len(phases) != 2 or len(timeouts) != 2 or len(capacities) != 1:
        return False
    for index in (0, 1):
        if not (phases[index].start() < timeouts[index].start()
                < markers[index].start()):
            return False
        if index == 1 and not markers[0].end() < phases[index].start():
            return False
    if not markers[1].end() < capacities[0].start() < markers[2].start():
        return False
    traceback = cleaned[markers[1].end():capacities[0].start()]
    return (
        "Traceback (most recent call last):" in traceback
        and "openai_codex/_run.py" in traceback
        and "raise RuntimeError(turn.error.message)" in traceback
    )


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
    if (evidence.error_code not in {TIMEOUT_CODE, MIXED_CAPACITY_CODE}
            or evidence.attempts != 3):
        raise RecoveryPreparationError("not an exact initial source failure")
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
    mixed = evidence.error_code == MIXED_CAPACITY_CODE
    detail = ("two 480-second turn_run timeouts, third model-capacity failure, commits 0"
              if mixed else "three 480-second initial turns, commits 0")
    observation = (
        "The exact job/step had two 480-second turn_run timeouts and a third "
        "non-retryable SDK model-capacity RuntimeError. This does not establish "
        "service recovery, a model-switch permission or a product code defect. "
        "Zero source commits/PR were verified. Avoid blind source reruns, "
        "credential changes or timeout increases."
        if mixed else
        "The trusted run's exact failed job/step and three bounded initial-turn "
        "markers were checked. This proves the initial source turn exhausted its "
        "deadline, **not** whether prompt size, service transport, authentication, "
        "resource limits or a code defect caused it. No previous committed source "
        "changes are accepted. Avoid blind reruns or increasing the timeout."
    )
    unresolved = ("Codex transport/model capacity has **not been proven recovered**"
                  if mixed else
                  "initial Codex transport timeout has **not been proven fixed**")
    owner_failure = ("초기 Codex 시간초과 2회 및 3차 모델 용량 오류"
                     if mixed else "초기 Codex 시간초과 3회")
    # Only trusted enumerated values are reflected; source_scope freeform text is
    # intentionally NOT included, so credentials embedded in prose never escape.
    doc = (
        f"# Failed source execution recovery — {evidence.task_id}\n\n"
        f"Status: **DOCS PR PREPARED / SOURCE NOT RETRIED**.\n\n"
        f"- Project: `{evidence.project}`\n"
        f"- Registered active branch: `{evidence.branch}`\n"
        f"- Original exact source SHA: `{evidence.source_sha}`\n"
        f"- Owner-started source run: `{evidence.runner_run_id}`\n"
        f"- Current approved stage: {stage_title}\n"
        f"- Failure code: `{evidence.error_code}` ({detail}).\n"
        f"- Source PR: **NOT CREATED**. Product source/Provider/EAS/device: **NOT_RUN**.\n\n"
        "## Evidence and uncertainty\n\n"
        f"{observation}\n\n"
        "## Owner recovery and registered next source stage\n\n"
        "1. **Review recovery:** the failed run produced no Product PR or committed "
        "source. Approved next source scope and stop gates remain unchanged. The "
        f"{unresolved}.\n"
        "2. **Owner document integration:** after exact PR-head CI, use the "
        "authenticated SANJINWORKS recovery document-merge button; this only "
        "integrates two docs/state files and verifies integrated SHA CI.\n"
        "3. **Optional Owner source click:** after successful document integration "
        "SANJINWORKS may show the original registered next task again. Only a "
        "fresh authenticated Owner click can run that source task. Repeated "
        "timeouts remain FAILED and require fresh review, not blind retries.\n\n"
        "## Preserved follow-on intent (not automatic permissions)\n\n"
        + "\n".join(f"- {title}" for title in next_titles)
        + "\n\n"
        "Do not delete or mutate old data, EAS/OTA, Cloudflare, credentials, "
        "permissions, production, or unfinished physical QA.\n"
    )
    current = copy.deepcopy(original_state)
    # Keep exact already-approved source scope and every successor stop gate.
    # A docs-only Owner merge creates a new Git HEAD; it does not claim Codex
    # readiness and does not dispatch development. Source remains Owner-started.
    prior_instruction = current["next_action"].get("owner_action", "")
    if not isinstance(prior_instruction, str):
        raise RecoveryPreparationError("unexpected approved Owner instruction")
    current["next_action"]["owner_action"] = (
        prior_instruction.rstrip()
        + f"\n복구 근거: Runner #{evidence.runner_run_id} {owner_failure}, "
        "소스 커밋·PR 0개. 복구 문서 병합과 CI 확인 후에도 실제 원인은 "
        "미해결일 수 있습니다. 동일 단계의 다음 작업은 SANJINWORKS Owner가 "
        "별도 클릭할 때만 실행하며 실패 시 무조건 자동 재실행하지 않습니다."
    )
    try:
        text = yaml.safe_dump(current, allow_unicode=True, sort_keys=False)
    except yaml.YAMLError as exc:
        raise RecoveryPreparationError("cannot serialize protected execution-state") from exc
    if len(doc) > 10_000 or len(text) > 14_000:
        raise RecoveryPreparationError("recovery documents exceed strict limits")
    return doc, text
