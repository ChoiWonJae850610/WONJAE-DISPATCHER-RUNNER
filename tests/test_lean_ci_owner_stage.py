"""Focused synthetic-only security tests for signed product CI staging."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.lean_ci_maintenance import LeanCiMaintenanceError
from wonjae_dispatcher_runner.lean_ci_owner_stage import (
    LeanCiOwnerStageError,
    SignedWorkflowSelection,
    sign_workflow_selection,
    stage_workflow,
    verify_workflow_selection,
)

KEY = "offline-unit-test-only-owner-attestation-key-64-characters"
OLD = """name: Future Validation
on:
  pull_request:
    branches: [dev-next]
  push:
    branches: [dev-next]
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v6
      - name: All history
        run: npm test
"""
LEAN = OLD.replace("run: npm test", "run: node tests/focused.mjs")
WORKFLOW = ".github/workflows/validate.yml"
REGISTRY = """schema_version: 1
projects:
  FUTURE:
    status: active
    repository: ExampleTeam/Future
    branch: dev-next
    ci:
      validation:
        name: Future Validation
        path: .github/workflows/validate.yml
  ARCHIVE:
    status: archived
"""


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _repo(path: Path, files: dict[str, str], origin: str) -> str:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "offline-test@example.invalid")
    _git(path, "config", "user.name", "CI Test")
    for name, content in files.items():
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "fixture")
    _git(path, "remote", "add", "origin", origin)
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def handoff(tmp_path: Path):
    control = tmp_path / "control"
    product = tmp_path / "product"
    control_sha = _repo(
        control, {"PROJECTS.yaml": REGISTRY},
        "https://github.com/ChoiWonJae850610/DEV-CONTROL.git",
    )
    product_sha = _repo(
        product, {WORKFLOW: OLD}, "https://github.com/ExampleTeam/Future.git",
    )
    selection = SignedWorkflowSelection(
        project="FUTURE",
        repository="ExampleTeam/Future",
        branch="dev-next",
        workflow_path=WORKFLOW,
        control_sha=control_sha,
        product_sha=product_sha,
        previous_sha256=hashlib.sha256(OLD.encode()).hexdigest(),
        proposed_sha256=hashlib.sha256(LEAN.encode()).hexdigest(),
        expires_ms=1_800_000_100_000,
        nonce="a" * 32,
    )
    return control, product, selection


def _stage(handoff, selection=None, candidate=LEAN, mac=None, secret=KEY):
    control, product, approved = handoff
    selected = selection or approved
    return stage_workflow(
        control_checkout=control,
        product_checkout=product,
        selection=selected,
        approval_mac=mac if mac is not None else sign_workflow_selection(selected, secret),
        approval_secret=secret,
        candidate=candidate,
        now_ms=1_800_000_000_000,
    )


def test_signed_owner_stages_only_registered_ci_in_disposable_checkout(handoff):
    control, product, selection = handoff
    before = _git(product, "rev-parse", "HEAD")
    received = _stage(handoff)
    assert received.status == "STAGED_LOCALLY_NOT_PUBLISHED"
    assert received.changed_paths == (WORKFLOW,)
    assert received.published is False and received.ci_started is False
    assert _git(product, "rev-parse", "HEAD") == before
    assert (product / WORKFLOW).read_text() == LEAN
    assert _git(control, "status", "--porcelain") == ""


def test_signed_claim_prevents_tampering_and_expiry(handoff):
    _, product, selection = handoff
    original_mac = sign_workflow_selection(selection, KEY)
    verify_workflow_selection(selection, original_mac, KEY, now_ms=1_800_000_000_000)
    for changed in (
        replace(selection, project="UNREGISTERED"),
        replace(selection, repository="SomeoneElse/Future"),
        replace(selection, workflow_path=".github/workflows/other.yml"),
        replace(selection, product_sha="b" * 40),
        replace(selection, proposed_sha256="0" * 64),
        replace(selection, expires_ms=1_799_999_999_999),
    ):
        with pytest.raises(LeanCiOwnerStageError):
            verify_workflow_selection(changed, original_mac, KEY, now_ms=1_800_000_000_000)
    assert _git(product, "status", "--porcelain") == ""


@pytest.mark.parametrize("replacement", [
    OLD.replace("contents: read", "contents: write"),
    OLD.replace("  pull_request:", "  pull_request_target:"),
    OLD.replace("runs-on: ubuntu-latest", "runs-on: self-hosted"),
    OLD.replace("uses: actions/checkout@v6", "uses: stranger/action@v1"),
])
def test_modified_permissions_triggers_or_actions_are_rejected(handoff, replacement):
    selection = handoff[2]
    modified = replace(selection, proposed_sha256=hashlib.sha256(replacement.encode()).hexdigest())
    with pytest.raises(LeanCiMaintenanceError):
        _stage(handoff, selection=modified, candidate=replacement)
    assert _git(handoff[1], "status", "--porcelain") == ""


def test_bad_signature_or_missing_key_cannot_write(handoff):
    with pytest.raises(LeanCiOwnerStageError):
        _stage(handoff, mac="0" * 64)
    with pytest.raises(LeanCiOwnerStageError):
        _stage(handoff, secret="short")
    assert _git(handoff[1], "status", "--porcelain") == ""


def test_wrong_project_registry_does_not_grant_a_workflow_path(handoff):
    selected = replace(handoff[2], project="OTHER")
    with pytest.raises(LeanCiOwnerStageError, match="active Git registry"):
        _stage(handoff, selection=selected)


def test_dirty_source_or_origin_mismatch_cannot_stage(handoff):
    _, product, _ = handoff
    (product / "README.md").write_text("dirty\n")
    with pytest.raises(LeanCiMaintenanceError, match="dirty"):
        _stage(handoff)
    (product / "README.md").unlink()
    _git(product, "remote", "set-url", "origin", "https://github.com/Other/Different.git")
    with pytest.raises(LeanCiMaintenanceError, match="origin"):
        _stage(handoff)


def test_candidate_digest_must_match_owner_review(handoff):
    with pytest.raises(LeanCiMaintenanceError, match="digest"):
        _stage(handoff, candidate=LEAN + "\n")
    assert _git(handoff[1], "status", "--porcelain") == ""


def test_no_old_content_or_noop_is_not_an_approval(handoff):
    selected = replace(
        handoff[2], proposed_sha256=hashlib.sha256(OLD.encode()).hexdigest()
    )
    with pytest.raises(LeanCiMaintenanceError, match="no changes"):
        _stage(handoff, selection=selected, candidate=OLD)
