"""Offline fail-closed admission tests for an inactive common PR workflow."""
import argparse
import hashlib
import importlib.util
from pathlib import Path

import pytest

FILE = Path(__file__).resolve().parents[1] / "scripts" / "common_control_pr_merge.py"
SPEC = importlib.util.spec_from_file_location("common_merge_guard", FILE)
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

REPOSITORY = "ChoiWonJae850610/DEV-CONTROL"
HEAD = "b" * 40
BASE = "a" * 40
REF = "docs/example"
PATHS = ["docs/example.md"]
DIGEST = hashlib.sha256("\n".join(PATHS).encode()).hexdigest()


def selected():
    return argparse.Namespace(repository=REPOSITORY, pr_number=123, head=HEAD,
                              base=BASE, ci_run_id=900, paths_digest=DIGEST,
                              expires_ms=100_000, nonce="c" * 32, approval_mac="d" * 64)


def candidate():
    return {"number": 123, "state": "open", "draft": False,
            "mergeable": True, "merged_at": None, "changed_files": 1,
            "user": {"login": "ChoiWonJae850610"},
            "head": {"sha": HEAD, "ref": REF, "repo": {"full_name": REPOSITORY}},
            "base": {"sha": "d" * 40, "ref": "main",
                     "repo": {"full_name": REPOSITORY}}}


def validation(**updates):
    return {"workflow_runs": [{
        "id": 900, "name": "DEV-CONTROL Validation", "head_sha": HEAD,
        "head_branch": REF, "event": "pull_request",
        "path": ".github/workflows/control-validation.yml",
        "status": "completed", "conclusion": "success", **updates,
    }]}


def test_missing_activation_and_separate_target_never_authorize():
    for environment in [
        {}, {"COMMON_PR_MERGE_ACTIVATED": "true"},
        {"COMMON_PR_MERGE_TARGET_REPOSITORY": REPOSITORY},
    ]:
        with pytest.raises(guard.AdmissionError):
            guard.guard_scope(selected(), environment)


def test_bad_repo_and_wrong_base_or_head_are_blocked():
    valid = candidate()
    assert guard.checked_pr(valid, selected()) == REF
    for changed in [
        {"number": 124}, {"draft": True}, {"mergeable": None},
        {"head": {**valid["head"], "sha": "f" * 40}},
        {"base": {**valid["base"], "ref": "master"}},
        {"head": {**valid["head"], "repo": {"full_name": "other/repo"}}},
    ]:
        with pytest.raises(guard.AdmissionError):
            guard.checked_pr({**valid, **changed}, selected())


def test_docs_only_inventory_and_exact_paths_checksum():
    guard.check_file_inventory([{"filename": "docs/example.md", "status": "modified"}], 1, DIGEST)
    for path in ["src/App.tsx", "docs/security.md", "docs/../x.md"]:
        with pytest.raises(guard.AdmissionError):
            guard.check_file_inventory([{"filename": path, "status": "modified"}], 1, DIGEST)
    with pytest.raises(guard.AdmissionError):
        guard.check_file_inventory(
            [{"filename": "docs/example.md", "status": "removed"}], 1, DIGEST
        )


def test_canonical_pr_head_run_must_be_latest_and_exact():
    good = validation()
    assert guard.exact_ci(good, selected(), "control-validation.yml",
        "DEV-CONTROL Validation", "pull_request", HEAD, REF) == 900
    for modified in [{"conclusion": "failure"}, {"head_sha": "f" * 40},
                     {"event": "push"}, {"status": "in_progress"}]:
        with pytest.raises(guard.AdmissionError):
            guard.exact_ci(validation(**modified), selected(), "control-validation.yml",
                "DEV-CONTROL Validation", "pull_request", HEAD, REF)


def test_unactivated_workflow_keeps_strict_default_branch_and_no_mutation_in_ci():
    text = Path(".github/workflows/common-control-pr-merge.yml").read_text("utf-8")
    assert "workflow_dispatch:" in text
    assert "github.actor == github.repository_owner" in text
    assert "COMMON_PR_MERGE_ACTIVATED == 'true'" in text
    assert "ref: main" in text and "persist-credentials: false" in text
    assert "permissions:\n  contents: read" in text
    assert "pull_request_target" not in text
    assert "runs-on: [self-hosted, Linux, X64, direct-worker]" in text
