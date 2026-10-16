"""Synthetic tests for the exact signed WAFL #88 one-YAML recovery."""

import base64
import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.lean_ci_owner_stage import SignedWorkflowSelection
from wonjae_dispatcher_runner.lean_ci_pr_publisher import (
    GitHubRest,
    LeanCiPublicationError,
    sign_publish_claim,
)
from wonjae_dispatcher_runner.lean_ci_pr_recovery import (
    ExactCiRecovery,
    recover_one_registered_ci_pr,
    sign_recovery_claim,
    verify_recovery_claim,
)

REPO = "ChoiWonJae850610/WAFL"
PATH = ".github/workflows/wafl-cloud-validation.yml"
KEY = "offline-synthetic-owner-pat-long-enough-for-hmac"
ORIGINAL = """name: WAFL Cloud Validation
on:
  pull_request:
    branches: [cloud-dev-v1]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v6
      - name: Full history
        run: npm test
"""
OLD_PR = ORIGINAL.replace("run: npm test", "run: node tests/obsolete-full-gate.mjs")
FIXED = ORIGINAL.replace("run: npm test", "run: node tests/affected-auth-rls.mjs")
ROOT_SHA = hashlib.sha256(ORIGINAL.encode()).hexdigest()
OLD_DIGEST = hashlib.sha256(OLD_PR.encode()).hexdigest()
NEW_DIGEST = hashlib.sha256(FIXED.encode()).hexdigest()
CONTROL = f"""schema_version: 1
projects:
  WAFL:
    status: active
    repository: {REPO}
    branch: cloud-dev-v1
    ci:
      validation:
        name: WAFL Cloud Validation
        path: {PATH}
"""


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _checkout(root, files, remote):
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Synthetic")
    _git(root, "config", "user.email", "synthetic@example.invalid")
    for path, data in files.items():
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(data, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    _git(root, "remote", "add", "origin", remote)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def fixture(tmp_path):
    control = tmp_path / "control"
    product = tmp_path / "product"
    control_sha = _checkout(
        control, {"PROJECTS.yaml": CONTROL},
        "https://github.com/ChoiWonJae850610/DEV-CONTROL.git",
    )
    source_sha = _checkout(product, {PATH: ORIGINAL}, "https://github.com/" + REPO + ".git")
    selected = SignedWorkflowSelection(
        project="WAFL", repository=REPO, branch="cloud-dev-v1",
        workflow_path=PATH, control_sha=control_sha, product_sha=source_sha,
        previous_sha256=ROOT_SHA, proposed_sha256=NEW_DIGEST,
        expires_ms=1_800_000_100_000, nonce="a" * 32,
    )
    scope = ExactCiRecovery(
        pr_number=88, pr_head_sha="b" * 40, failed_run_id=456,
        current_workflow_sha256=OLD_DIGEST,
    )
    return control, product, selected, scope


class FakeApi:
    def __init__(self, selected, scope):
        self.selection = selected
        self.scope = scope
        self.operations = []
        self.pr_head = scope.pr_head_sha
        self.extra_open = False
        self.has_other_files = False
        self.failed_ci = True
        self.branch = ("lean-ci/wafl-" + selected.product_sha[:12]
                       + "-" + scope.current_workflow_sha256[:12])
        self.new_sha = "e" * 40

    def get(self, path, *, missing_ok=False):
        self.operations.append(("GET", path))
        root = "repos/" + REPO + "/"
        if path == root + "branches/cloud-dev-v1":
            return {"commit": {"sha": self.selection.product_sha}}
        if path.startswith(root + "pulls?state=open&base="):
            return [{"number": 88}, {"number": 999}] if self.extra_open else [{"number": 88}]
        if path == root + "pulls/88":
            return {
                "number": 88, "state": "open", "merged": False, "draft": False,
                "html_url": "https://github.com/" + REPO + "/pull/88",
                "head": {"sha": self.pr_head, "ref": self.branch, "repo": {"full_name": REPO}},
                "base": {"sha": self.selection.product_sha, "ref": "cloud-dev-v1",
                         "repo": {"full_name": REPO}},
                "body": (
                    "- Control SHA: " + self.selection.control_sha + "\n"
                    "- Product base SHA: " + self.selection.product_sha + "\n"
                    "- Workflow: " + PATH + "\n"
                    "- Old SHA256: " + ROOT_SHA + "\n"
                    "- New SHA256: " + OLD_DIGEST
                ),
            }
        if path == root + "pulls/88/files?per_page=100":
            result = [{"filename": PATH, "status": "modified"}]
            if self.has_other_files:
                return result + [{"filename": "app/data.ts", "status": "modified"}]
            return result
        if path == root + "actions/runs/456":
            return {
                "id": 456, "head_sha": self.scope.pr_head_sha,
                "name": "WAFL Cloud Validation", "path": PATH,
                "event": "pull_request", "status": "completed",
                "conclusion": "failure" if self.failed_ci else "success",
                "repository": {"full_name": REPO},
            }
        if path == root + "contents/" + PATH + "?ref=" + self.scope.pr_head_sha:
            return {"encoding": "base64",
                    "content": base64.b64encode(OLD_PR.encode()).decode()}
        if path == root + "git/ref/heads/" + self.branch:
            return {"object": {"sha": self.pr_head}}
        if path == root + "git/commits/" + self.scope.pr_head_sha:
            return {"tree": {"sha": "a" * 40},
                    "parents": [{"sha": self.selection.product_sha}]}
        raise AssertionError("unexpected GET " + path)

    def post(self, path, payload):
        self.operations.append(("POST", path))
        if path.endswith("/git/blobs"):
            assert payload == {"content": FIXED, "encoding": "utf-8"}
            return {"sha": "c" * 40}
        if path.endswith("/git/trees"):
            assert payload["tree"] == [{"path": PATH, "mode": "100644",
                                       "type": "blob", "sha": "c" * 40}]
            return {"sha": "d" * 40}
        if path.endswith("/git/commits"):
            assert payload["parents"] == [self.scope.pr_head_sha]
            return {"sha": self.new_sha}
        raise AssertionError("unexpected POST " + path)

    def patch(self, path, payload):
        self.operations.append(("PATCH", path))
        assert path == "repos/" + REPO + "/git/refs/heads/" + self.branch
        assert payload == {"sha": self.new_sha, "force": False}
        self.pr_head = self.new_sha
        return {"object": {"sha": self.new_sha}}


def execute(fixture, api=None, scope=None, mac=None, candidate=FIXED):
    control, product, selection, approved = fixture
    chosen = scope or approved
    client = api or FakeApi(selection, chosen)
    return recover_one_registered_ci_pr(
        control_checkout=control, product_checkout=product,
        selection=selection, publish_mac=sign_publish_claim(selection, KEY),
        recovery=chosen,
        recovery_mac=mac if mac is not None else sign_recovery_claim(selection, chosen, KEY),
        approval_secret=KEY,
        candidate=candidate, api=client, now_ms=1_800_000_000_000,
    )


def test_exact_signed_ci_resume_only_fast_forwards_existing_pr_one_workflow(fixture):
    _, product, selection, scope = fixture
    api = FakeApi(selection, scope)
    result = execute(fixture, api)
    assert result.pr_number == 88
    assert result.pr_head_sha == api.new_sha
    assert result.status == "EXISTING_CI_PR_REPAIRED_VALIDATION_PENDING"
    assert result.canonical_ci_verified is False and result.merged is False
    assert [method for method, _ in api.operations if method != "GET"] == [
        "POST", "POST", "POST", "PATCH",
    ]
    assert _git(product, "rev-parse", "HEAD") == selection.product_sha
    assert _git(product, "status", "--porcelain") == "M " + PATH


@pytest.mark.parametrize("change", [
    "extra_open", "has_other_files", "failed_ci",
])
def test_writer_collision_wrong_files_or_nonfailing_ci_cannot_repair(fixture, change):
    _, _, selection, scope = fixture
    api = FakeApi(selection, scope)
    setattr(api, change, False if change == "failed_ci" else True)
    with pytest.raises(LeanCiPublicationError):
        execute(fixture, api)
    assert not [m for m, _ in api.operations if m in {"POST", "PATCH"}]


def test_recovery_is_signed_separately_and_tampering_cannot_write(fixture):
    _, _, selection, scope = fixture
    token = sign_recovery_claim(selection, scope, KEY)
    verify_recovery_claim(selection, scope, token, KEY, now_ms=1_800_000_000_000)
    for changed in (
        replace(scope, pr_number=89), replace(scope, pr_head_sha="c" * 40),
        replace(scope, failed_run_id=457),
        replace(scope, current_workflow_sha256="d" * 64),
    ):
        with pytest.raises(LeanCiPublicationError):
            verify_recovery_claim(
                selection, changed, token, KEY, now_ms=1_800_000_000_000,
            )
    api = FakeApi(selection, scope)
    with pytest.raises(LeanCiPublicationError):
        execute(fixture, api, mac="0" * 64)
    assert not [m for m, _ in api.operations if m in {"POST", "PATCH"}]


def test_one_click_runner_concurrency_is_per_registered_project():
    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/lean-ci-owner-publish.yml").read_text()
    assert "lean-ci-owner-publish-${{ github.repository }}-${{ inputs.project }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "LEAN_INPUT_PROJECT: ${{ inputs.project }}" in workflow
    assert "LEAN_RECOVERY_JSON: ${{ inputs.recovery_json }}" in workflow
    assert "LEAN_RECOVERY_MAC: ${{ inputs.recovery_mac }}" in workflow
    assert "--check-claim" in workflow
    assert "-f sha=\"$LEAN_PR_HEAD\"" in workflow


def test_force_push_and_unregistered_ref_rejected_before_transport(monkeypatch):
    api = GitHubRest(REPO, "synthetic-read-only-owner-token")
    monkeypatch.setattr(api, "_request", lambda *args, **kwargs: "should not run")
    root = "repos/" + REPO + "/git/refs/heads/lean-ci/wafl-"
    with pytest.raises(LeanCiPublicationError):
        api.patch(root + "abc", {"sha": "a" * 40, "force": True})
    with pytest.raises(LeanCiPublicationError):
        api.patch("repos/" + REPO + "/git/refs/heads/main",
                  {"sha": "a" * 40, "force": False})


def test_signed_muvel_12_scope_is_distinct_and_rejects_any_other_writer(fixture):
    _, _, original, _ = fixture
    selection = replace(
        original,
        project="MUVEL",
        repository="ChoiWonJae850610/MUVEL",
        workflow_path=".github/workflows/cloud-dev.yml",
    )
    scope = ExactCiRecovery(
        pr_number=12,
        pr_head_sha="556d2fd73f1fe546c67c87bdd114853ae3d9b859",
        failed_run_id=38978061443,
        current_workflow_sha256=(
            "5e119c74fb950b1c14c009163bc201fb4967d46b4158b63131a3a55e33a0a9a9"
        ),
    )
    mac = sign_recovery_claim(selection, scope, KEY)
    verify_recovery_claim(selection, scope, mac, KEY, now_ms=1_800_000_000_000)
    assert mac != sign_recovery_claim(
        original,
        replace(scope, pr_number=88), KEY,
    )
    for changed in (
        replace(scope, pr_number=13),
        replace(scope, failed_run_id=38978061444),
        replace(scope, pr_head_sha="c" * 40),
        replace(scope, current_workflow_sha256="f" * 64),
    ):
        with pytest.raises(LeanCiPublicationError):
            verify_recovery_claim(
                selection, changed, mac, KEY, now_ms=1_800_000_000_000,
            )
    for other in (
        replace(selection, repository="ChoiWonJae850610/CLASSMO"),
        replace(selection, project="ESC"),
        replace(selection, workflow_path=".github/workflows/other.yml"),
    ):
        with pytest.raises(LeanCiPublicationError):
            sign_recovery_claim(other, scope, KEY)


def test_exact_muvel_resume_scope_stays_one_ci_file_and_never_force_push(fixture):
    _, _, original, scope = fixture
    selection = replace(
        original,
        project="MUVEL",
        repository="ChoiWonJae850610/MUVEL",
        workflow_path=".github/workflows/cloud-dev.yml",
    )
    muvel = replace(scope, pr_number=12)
    api = FakeApi(original, scope)
    # A product mismatch must reject before any Git write; historical WAFL
    # fixture and its PR may not become authorization for MUVEL.
    with pytest.raises(LeanCiPublicationError):
        recover_one_registered_ci_pr(
            control_checkout=fixture[0],
            product_checkout=fixture[1],
            selection=selection,
            publish_mac=sign_publish_claim(selection, KEY),
            recovery=muvel,
            recovery_mac=sign_recovery_claim(selection, muvel, KEY),
            approval_secret=KEY,
            candidate=FIXED,
            api=api,
            now_ms=1_800_000_000_000,
        )
    assert not [method for method, _ in api.operations if method in {"POST", "PATCH"}]
