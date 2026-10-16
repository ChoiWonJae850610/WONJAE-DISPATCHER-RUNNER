"""Signed, exact-scope staging of one registered canonical CI workflow.

This trusted primitive stages content in an isolated, disposable product
checkout only. It NEVER pushes, creates/merges a PR, runs product validation,
changes credentials or executes edited workflow commands. Separate approved
Owner-triggered GitHub Actions and a future trusted publication stage are needed.
Ordinary Direct Worker .github/** protection is deliberately unaffected.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .lean_ci_maintenance import (
    LeanCiMaintenanceError,
    WorkflowPreview,
    _require_clean,
    registered_ci,
    review_candidate,
)

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_MAX_AGE_MS = 10 * 60 * 1000
_INT64_MAX = (1 << 53) - 1
_PREFIX = "registered-lean-ci-maintenance-v1"


class LeanCiOwnerStageError(LeanCiMaintenanceError):
    """Fail closed before touching the product checkout."""


@dataclass(frozen=True)
class SignedWorkflowSelection:
    project: str
    repository: str
    branch: str
    workflow_path: str
    control_sha: str
    product_sha: str
    previous_sha256: str
    proposed_sha256: str
    expires_ms: int
    nonce: str


@dataclass(frozen=True)
class StagingReceipt:
    status: str
    project: str
    workflow_path: str
    product_sha: str
    control_sha: str
    previous_sha256: str
    proposed_sha256: str
    changed_paths: tuple[str, ...]
    published: bool = False
    ci_started: bool = False


def _valid_selection(value: SignedWorkflowSelection) -> None:
    if not isinstance(value.project, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_-]{0,63}", value.project
    ):
        raise LeanCiOwnerStageError("invalid registered project identity")
    if not isinstance(value.repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value.repository
    ):
        raise LeanCiOwnerStageError("invalid registered repository")
    if not isinstance(value.branch, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", value.branch
    ) or ".." in value.branch:
        raise LeanCiOwnerStageError("invalid registered branch")
    if not isinstance(value.workflow_path, str) or not re.fullmatch(
        r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", value.workflow_path
    ):
        raise LeanCiOwnerStageError("workflow must be an exact canonical path")
    if any(not isinstance(v, str) or not _HEX40.fullmatch(v) for v in (
        value.control_sha, value.product_sha
    )):
        raise LeanCiOwnerStageError("invalid Git commit SHA")
    if any(not isinstance(v, str) or not _HEX64.fullmatch(v) for v in (
        value.previous_sha256, value.proposed_sha256
    )):
        raise LeanCiOwnerStageError("invalid reviewed workflow digest")
    if (not isinstance(value.expires_ms, int) or isinstance(value.expires_ms, bool)
            or value.expires_ms < 1 or value.expires_ms > _INT64_MAX
            or not isinstance(value.nonce, str) or not _NONCE.fullmatch(value.nonce)):
        raise LeanCiOwnerStageError("invalid expiry or nonce")


def _payload(value: SignedWorkflowSelection) -> bytes:
    _valid_selection(value)
    text = "\n".join((
        _PREFIX, value.project, value.repository, value.branch, value.workflow_path,
        value.control_sha, value.product_sha, value.previous_sha256,
        value.proposed_sha256, str(value.expires_ms), value.nonce,
    ))
    return text.encode("utf-8")


def sign_workflow_selection(value: SignedWorkflowSelection, secret: str) -> str:
    """Only for synthetic tests or the independently authenticated Owner Worker."""
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise LeanCiOwnerStageError("missing dedicated Owner approval key")
    return hmac.new(secret.encode("utf-8"), _payload(value), hashlib.sha256).hexdigest()


def verify_workflow_selection(
    value: SignedWorkflowSelection,
    mac: str,
    secret: str,
    *,
    now_ms: int | None = None,
) -> None:
    """Never infer Owner authority from a plain Actions workflow_dispatch input."""
    clock = int(time.time() * 1000) if now_ms is None else now_ms
    if not isinstance(clock, int) or clock < 1:
        raise LeanCiOwnerStageError("invalid monotonic admission clock")
    if value.expires_ms < clock or value.expires_ms > clock + _MAX_AGE_MS:
        raise LeanCiOwnerStageError("Owner approval has expired or exceeds validity")
    if not isinstance(mac, str) or not _HEX64.fullmatch(mac):
        raise LeanCiOwnerStageError("missing Owner attestation")
    expected = sign_workflow_selection(value, secret)
    if not hmac.compare_digest(mac, expected):
        raise LeanCiOwnerStageError("Owner attestation does not match exact selection")


def _changed(repo: Path) -> tuple[str, ...]:
    try:
        text = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all", "-z"],
            capture_output=True, check=True, timeout=20,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise LeanCiOwnerStageError("staged checkout could not be inspected") from exc
    # Rename/status entries with a second NUL path are not admissible.
    values = text.split("\0")
    changed = []
    for row in filter(None, values):
        if len(row) < 4 or row[:2] not in {" M", "M ", "MM"}:
            raise LeanCiOwnerStageError("unexpected Git modification class")
        changed.append(row[3:])
    return tuple(changed)


def stage_workflow(
    *,
    control_checkout: Path,
    product_checkout: Path,
    selection: SignedWorkflowSelection,
    approval_mac: str,
    approval_secret: str,
    candidate: str,
    now_ms: int | None = None,
) -> StagingReceipt:
    """Locally stage only the registry-owned validation YAML, never publish it."""
    verify_workflow_selection(selection, approval_mac, approval_secret, now_ms=now_ms)
    _require_clean(control_checkout)
    routes = registered_ci(control_checkout / "PROJECTS.yaml")
    route = routes.get(selection.project)
    if route is None or (
        route.repository != selection.repository
        or route.branch != selection.branch
        or route.validation_path != selection.workflow_path
    ):
        raise LeanCiOwnerStageError("Owner choice does not match active Git registry")
    reviewed: WorkflowPreview = review_candidate(
        control_checkout=control_checkout,
        product_checkout=product_checkout,
        project=selection.project,
        expected_control_sha=selection.control_sha,
        expected_product_sha=selection.product_sha,
        expected_previous_sha256=selection.previous_sha256,
        expected_proposed_sha256=selection.proposed_sha256,
        candidate=candidate,
    )
    if reviewed.workflow_path != selection.workflow_path:
        raise LeanCiOwnerStageError("registered workflow changed during review")
    path = product_checkout / reviewed.workflow_path
    if path.is_symlink() or not path.is_file():
        raise LeanCiOwnerStageError("registered workflow is not a regular file")
    # Preserve the exact reviewed old digest against an ordinary write race.
    if hashlib.sha256(path.read_bytes()).hexdigest() != selection.previous_sha256:
        raise LeanCiOwnerStageError("workflow content changed after review")
    path.write_text(candidate, encoding="utf-8")
    changed = _changed(product_checkout)
    if changed != (reviewed.workflow_path,):
        raise LeanCiOwnerStageError("staging modified unexpected product paths")
    if hashlib.sha256(path.read_bytes()).hexdigest() != selection.proposed_sha256:
        raise LeanCiOwnerStageError("written workflow differs from reviewed digest")
    return StagingReceipt(
        status="STAGED_LOCALLY_NOT_PUBLISHED",
        project=reviewed.project,
        workflow_path=reviewed.workflow_path,
        product_sha=reviewed.source_sha,
        control_sha=reviewed.control_sha,
        previous_sha256=reviewed.previous_sha256,
        proposed_sha256=reviewed.proposed_sha256,
        changed_paths=changed,
    )
