"""No-credential strict Owner document-merge guard and workflow regression tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "document_pr_merge.py"
SPEC = importlib.util.spec_from_file_location("document_merge_guard", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

HEAD = "b" * 40
BASE = "a" * 40


def route():
    return {
        "repo": "ChoiWonJae850610/CLASSMO",
        "branch": "cloud-dev-v1",
        "workflow": ".github/workflows/classmo-cloud-validation.yml",
        "workflow_name": "CLASSMO Cloud Validation",
        "state_path": ".wonjae/execution-state.yaml",
    }


def registry():
    return {
        "projects": {
            "CLASSMO": {
                "status": "active",
                "repository": route()["repo"],
                "branch": route()["branch"],
                "ci": {"validation": {
                    "path": route()["workflow"], "name": route()["workflow_name"],
                }},
                "execution": {
                    "mode": "direct_worker",
                    "state_path": ".wonjae/execution-state.yaml",
                },
            },
        },
    }


def pr():
    return {
        "number": 110,
        "state": "open",
        "draft": False,
        "merged_at": None,
        "mergeable": True,
        "changed_files": 3,
        "user": {"login": "ChoiWonJae850610"},
        "head": {"sha": HEAD, "repo": {"full_name": route()["repo"]}},
        "base": {"ref": "cloud-dev-v1", "sha": BASE,
                 "repo": {"full_name": route()["repo"]}},
    }


def files():
    return [
        {"filename": ".wonjae/execution-state.yaml", "status": "modified", "changes": 12},
        {"filename": "docs/NEXT_WORK.md", "status": "modified", "changes": 15},
        {"filename": "docs/IOS_PREVIEW_012_BUILD.md", "status": "modified", "changes": 6},
    ]


def runs(conclusion="success", status="completed"):
    return {"workflow_runs": [{
        "id": 123, "head_sha": HEAD, "event": "pull_request",
        "path": route()["workflow"], "name": route()["workflow_name"],
        "conclusion": conclusion, "status": status,
    }]}


def test_exact_prepared_document_pr_passes_guards():
    assert guard.route_from_registry(registry(), "CLASSMO") == route()
    guard.check_pr(pr(), route(), 110, HEAD, BASE)
    guard.check_files(files(), 3, route()["state_path"])
    assert guard.check_pr_head_validation(runs(), route(), HEAD) == 123
    guard.check_integrated_commit(
        {"sha": "c" * 40, "parents": [{"sha": BASE}, {"sha": HEAD}]},
        BASE, HEAD, "c" * 40,
    )


@pytest.mark.parametrize("path", [
    "src/App.tsx", "apps/mobile/app.json", ".github/workflows/validate.yml",
    ".wonja/execution-state.yaml", ".wonjae/other.yaml", "../docs/pwn.md",
    "docs/../../src/App.tsx", "docs//NEXT_WORK.md", "docs/", "",
])
def test_non_docs_paths_rejected(path):
    assert not guard.allowed_document_path(path, ".wonjae/execution-state.yaml")


@pytest.mark.parametrize("mutation", [
    {"status": "removed"}, {"status": "renamed", "previous_filename": "docs/old.md"},
    {"status": "copied"}, {"status": "changed"},
])
def test_removed_renamed_and_unknown_modifications_fail(mutation):
    with pytest.raises(guard.DocumentMergeError):
        guard.check_files([{**files()[0], **mutation}], 1, route()["state_path"])


@pytest.mark.parametrize("attr,value", [
    ("state", "closed"), ("draft", True), ("merged_at", "2026-10-09T06:00:00Z"),
    ("mergeable", False), ("changed_files", 101),
])
def test_non_open_or_unverified_pr_refused(attr, value):
    data = {**pr(), attr: value}
    with pytest.raises(guard.DocumentMergeError):
        guard.check_pr(data, route(), 110, HEAD, BASE)


def test_changed_sha_owner_or_fork_refused():
    for value in (
        {"head": {"sha": "c" * 40, "repo": {"full_name": route()["repo"]}}},
        {"base": {**pr()["base"], "sha": "d" * 40}},
        {"head": {"sha": HEAD, "repo": {"full_name": "elsewhere/fork"}}},
        {"user": {"login": "attacker"}},
    ):
        with pytest.raises(guard.DocumentMergeError):
            guard.check_pr({**pr(), **value}, route(), 110, HEAD, BASE)


def test_registered_route_only_no_kdn_main_or_forged_state():
    with pytest.raises(guard.DocumentMergeError):
        guard.route_from_registry(registry(), "KDN")
    for change in (
        {"branch": "main"},
        {"execution": {"mode": "direct_worker", "state_path": "other"}},
        {"repository": "../../bad"},
    ):
        wrong = registry()
        wrong["projects"]["CLASSMO"].update(change)
        with pytest.raises(guard.DocumentMergeError):
            guard.route_from_registry(wrong, "CLASSMO")


def test_missing_pending_latest_ci_fails_closed_even_with_older_pass():
    with pytest.raises(guard.DocumentMergeError):
        guard.check_pr_head_validation(runs(status="in_progress", conclusion=None), route(), HEAD)
    with pytest.raises(guard.DocumentMergeError):
        guard.check_pr_head_validation({"workflow_runs": []}, route(), HEAD)
    with pytest.raises(guard.DocumentMergeError):
        guard.check_pr_head_validation({
            "workflow_runs": [
                runs()["workflow_runs"][0],
                {**runs(status="in_progress", conclusion=None)["workflow_runs"][0], "id": 124},
            ],
        }, route(), HEAD)


def test_wrong_ancestry_or_unrelated_sha_cannot_claim_integrated_pass():
    with pytest.raises(guard.DocumentMergeError):
        guard.check_integrated_commit(
            {"sha": "c" * 40, "parents": [{"sha": HEAD}, {"sha": BASE}]},
            BASE, HEAD, "c" * 40,
        )
    with pytest.raises(guard.DocumentMergeError):
        guard.check_integrated_commit(
            {"sha": "d" * 40, "parents": [{"sha": BASE}, {"sha": HEAD}]},
            BASE, HEAD, "c" * 40,
        )


def test_trusted_owner_button_workflow_has_separate_tokens_and_no_provider_calls():
    root = Path(".github/workflows")
    caller = (root / "document-pr-merge.yml").read_text(encoding="utf-8")
    core = (root / "document-pr-merge-core.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in caller
    assert 'run-name: "Document PR Merge ${{ inputs.project }} #${{ inputs.pr_number }}' in caller
    assert "github.actor == github.repository_owner" in caller
    for token in ("WAFL_WRITE_TOKEN", "CLASSMO_WRITE_TOKEN", "ESC_WRITE_TOKEN",
                  "MUVEL_WRITE_TOKEN", "CONTROL_READ_TOKEN"):
        assert token in caller
    assert "secrets: inherit" not in caller
    assert "ref: main" in core
    assert "Refuse stale shared control registration" in core
    assert "scripts/document_pr_merge.py" in core
    assert "--control-sha" in core
    assert "eas build" not in core.lower()
    assert "eas update" not in core.lower()
    assert "npm ci" not in core.lower()
    assert "deploy" not in core.lower()
