"""Offline exact SHA+patch-digest and HMAC admission regression; no actual dispatch."""
import argparse
import hashlib
import hmac
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "common_control_pr_merge.py"
SPEC = importlib.util.spec_from_file_location("common_merge_guard", SCRIPT)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

REPO = "ChoiWonJae850610/DEV-CONTROL"
HEAD = "b" * 40
BASE = "a" * 40
REF = "docs/example"
PATH = "docs/COMMON_PR_PILOT_NOTE.md"
PATCH = "@@ -0,0 +1,2 @@\n+Owner approval rehearsal\n+No operational changes"
PATH_DIGEST = "d1e391055c364f505e6a7d0660a0cb4c39fa63609ca17950aae6df1d25068055"
DIFF_DIGEST = "d6aa12d4fd109dbc8ca564eaa8d362226243fbb43b824814205e9b6fa4ae6ea3"
HMAC_VECTOR = "9084ce021789754138d8b87665dd2fa094ab6743178f377470aae64881182fca"
SIGNING_SECRET = "synthetic-owner-approval-attestation-secret-not-real"


def selected(**changes):
    result = argparse.Namespace(
        repository=REPO, pr_number=123, head=HEAD, base=BASE, ci_run_id=900,
        paths_digest=PATH_DIGEST, diff_digest=DIFF_DIGEST,
        expires_ms=1800000000123, nonce="c" * 32, approval_mac=HMAC_VECTOR,
    )
    for key, value in changes.items():
        setattr(result, key, value)
    return result


def candidate(**changes):
    pr = {
        "number": 123, "state": "open", "merged_at": None, "draft": False,
        "mergeable": True, "changed_files": 1,
        "user": {"login": "ChoiWonJae850610"},
        "head": {"sha": HEAD, "ref": REF, "repo": {"full_name": REPO}},
        "base": {"sha": "d" * 40, "ref": "main", "repo": {"full_name": REPO}},
    }
    return {**pr, **changes}


def file_change(**changes):
    return {"filename": PATH, "status": "added", "patch": PATCH, "changes": 2, **changes}


def validation(**changes):
    return {"total_count": 1, "workflow_runs": [{
        "id": 900, "name": "DEV-CONTROL Validation", "head_sha": HEAD,
        "head_branch": REF, "event": "pull_request",
        "path": ".github/workflows/control-validation.yml",
        "status": "completed", "conclusion": "success", **changes,
    }]}


def test_dormant_credentials_and_one_target_common_repo_guard():
    for runtime in [{}, {"COMMON_PR_MERGE_ACTIVATED": "true"}, {
        "COMMON_PR_MERGE_TARGET_REPOSITORY": REPO,
        "GITHUB_REPOSITORY_OWNER": "ChoiWonJae850610",
    }]:
        with pytest.raises(guard.AdmissionError):
            guard.guard_scope(selected(), runtime)
    config = {
        "COMMON_PR_MERGE_ACTIVATED": "true",
        "COMMON_PR_MERGE_TARGET_REPOSITORY": REPO,
        "GITHUB_REPOSITORY_OWNER": "ChoiWonJae850610",
        "GH_TOKEN": "synthetic-only", "COMMON_PR_MERGE_APPROVAL_SECRET": SIGNING_SECRET,
    }
    assert guard.guard_scope(selected(), config) == (
        "control-validation.yml", "DEV-CONTROL Validation"
    )
    for other in ["ChoiWonJae850610/SANJINWORKS", "ChoiWonJae850610/KDN",
                  "ChoiWonJae850610/WAFL"]:
        with pytest.raises(guard.AdmissionError):
            guard.guard_scope(selected(repository=other), {
                **config, "COMMON_PR_MERGE_TARGET_REPOSITORY": other,
            })
    runner = "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER"
    assert guard.guard_scope(selected(repository=runner), {
        **config, "COMMON_PR_MERGE_TARGET_REPOSITORY": runner,
    }) == ("validate.yml", "Runner Validation")


def test_hmac_interoperability_vector_identical_to_typescript():
    assert hashlib.sha256(PATH.encode()).hexdigest() == PATH_DIGEST
    assert hashlib.sha256(f"{PATH}\nadded\n{PATCH}".encode()).hexdigest() == DIFF_DIGEST
    assert hmac.new(SIGNING_SECRET.encode(), guard.canonical(selected()),
                    hashlib.sha256).hexdigest() == HMAC_VECTOR
    guard.verify_owner_receipt(selected(), SIGNING_SECRET, now_ms=1800000000000)
    for changes in [
        {"head": "f" * 40}, {"base": "f" * 40}, {"diff_digest": "0" * 64},
        {"paths_digest": "0" * 64}, {"ci_run_id": 901}, {"nonce": "e" * 32},
    ]:
        with pytest.raises(guard.AdmissionError):
            guard.verify_owner_receipt(selected(**changes), SIGNING_SECRET,
                                       now_ms=1800000000000)
    with pytest.raises(guard.AdmissionError):
        guard.verify_owner_receipt(selected(), SIGNING_SECRET, now_ms=1800000000123)


def test_stale_fork_draft_unmergeable_rejected():
    assert guard.checked_pr(candidate(), selected()) == REF
    for changed in [
        {"number": 124}, {"draft": True}, {"mergeable": False},
        {"merged_at": "2026-10-11"}, {"state": "closed"},
        {"head": {**candidate()["head"], "sha": "f" * 40}},
        {"head": {**candidate()["head"], "repo": {"full_name": "fork/repo"}}},
        {"base": {**candidate()["base"], "ref": "master"}},
        {"user": {"login": "untrusted"}},
    ]:
        with pytest.raises(guard.AdmissionError):
            guard.checked_pr(candidate(**changed), selected())


def test_signed_patch_contents_cannot_change_even_when_path_stays_identical():
    guard.check_file_inventory([file_change()], 1, PATH_DIGEST, DIFF_DIGEST, REPO)
    for changed in [
        {"filename": "docs/CHATGPT_OPERATING_RULES.md"},
        {"filename": "docs/SELF_HOSTED_EXECUTION.md"},
        {"filename": "src/worker.ts"}, {"filename": ".github/workflows/validate.yml"},
        {"filename": "src/common_control_pr_merge.py"},
        {"filename": "docs/../COMMON_PR_PILOT_NOTE.md"},
        {"status": "removed"}, {"status": "renamed"},
        {"patch": PATCH + "\n+unreviewed"}, {"patch": None},
        {"previous_filename": "docs/old.md"},
    ]:
        with pytest.raises(guard.AdmissionError):
            guard.check_file_inventory([file_change(**changed)], 1, PATH_DIGEST, DIFF_DIGEST, REPO)
    with pytest.raises(guard.AdmissionError):
        guard.check_file_inventory([file_change()], 2, PATH_DIGEST, DIFF_DIGEST, REPO)


def test_routine_multi_file_diff_signed_in_canonical_path_order():
    files = [
        {"filename": "src/display_format.py", "status": "modified",
         "patch": "@@ -1 +1 @@\n-print('old')\n+print('new')", "changes": 2},
        {"filename": "docs/README.md", "status": "added",
         "patch": "@@ -0,0 +1 @@\n+Notes", "changes": 1},
        {"filename": "tests/test_display.py", "status": "added",
         "patch": "@@ -0,0 +1 @@\n+assert True", "changes": 1},
    ]
    paths = sorted(f["filename"] for f in files)
    path_digest = hashlib.sha256("\n".join(paths).encode()).hexdigest()
    ordered = sorted(files, key=lambda f: f["filename"])
    records = [f"{f['filename']}\n{f['status']}\n{f['patch']}" for f in ordered]
    diff_digest = hashlib.sha256("\n---\n".join(records).encode()).hexdigest()
    guard.check_file_inventory(files, 3, path_digest, diff_digest, REPO)
    guard.check_file_inventory(list(reversed(files)), 3, path_digest, diff_digest, REPO)
    with pytest.raises(guard.AdmissionError):
        guard.check_file_inventory(
            [{**files[0], "patch": files[0]["patch"] + "\n+extra"}, *files[1:]],
            3, path_digest, diff_digest, REPO
        )
    with pytest.raises(guard.AdmissionError):
        guard.check_file_inventory(files, 3, path_digest, diff_digest, "other/repo")
    with pytest.raises(guard.AdmissionError):
        guard.check_file_inventory(files + [files[0]], 4, path_digest, diff_digest, REPO)


def test_latest_exact_pr_head_ci_only():
    assert guard.exact_ci(validation(), selected(), "control-validation.yml",
        "DEV-CONTROL Validation", "pull_request", HEAD, REF) == 900
    for changed in [{"conclusion": "failure"}, {"event": "push"},
                    {"head_sha": "f" * 40}, {"head_branch": "other"},
                    {"status": "in_progress"}, {"name": "Other"}]:
        with pytest.raises(guard.AdmissionError):
            guard.exact_ci(validation(**changed), selected(), "control-validation.yml",
                           "DEV-CONTROL Validation", "pull_request", HEAD, REF)


def test_workflow_secret_boundary_and_runner_prewrite_checks():
    workflow = Path(".github/workflows/common-control-pr-merge.yml").read_text("utf-8")
    assert "workflow_dispatch:" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "vars.COMMON_PR_MERGE_ACTIVATED == 'true'" in workflow
    assert "runs-on: [self-hosted, Linux, X64, direct-worker]" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "pull_request_target" not in workflow
    assert "inputs.diff_digest" in workflow
    assert "--diff-digest" in workflow
    assert "secrets.OWNER_GITHUB_TOKEN" in workflow
    assert "secrets.COMMON_PR_MERGE_TOKEN" not in workflow
    assert "secrets.COMMON_PR_MERGE_APPROVAL_SECRET" in workflow
    source = SCRIPT.read_text("utf-8")
    assert "latest_files = gh_api(" in source
    assert "exact_ci(ci_runs(args.repository" in source
