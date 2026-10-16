"""Small synthetic tests for the one-workflow Owner-signed draft PR publisher."""

import hashlib
import subprocess
from pathlib import Path
from dataclasses import replace

import pytest

from wonjae_dispatcher_runner.lean_ci_owner_stage import SignedWorkflowSelection
from wonjae_dispatcher_runner.lean_ci_pr_publisher import (
    LeanCiPublicationError,
    publish_lean_ci_pr,
    sign_publish_claim,
    verify_publish_claim,
)

KEY = "synthetic-owner-maintenance-signing-key-not-a-production-secret"
REPO = "ExampleTeam/Future"
WORKFLOW = ".github/workflows/validate.yml"
OLD = """name: Future CI
on:
  pull_request:
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
      - name: Full regression
        run: npm test
"""
NEW = OLD.replace("run: npm test", "run: node tests/affected.mjs")
REGISTRY = """schema_version: 1
projects:
  FUTURE:
    status: active
    repository: ExampleTeam/Future
    branch: dev-next
    ci:
      validation:
        name: Future CI
        path: .github/workflows/validate.yml
  ARCHIVED:
    status: archived
"""


def _git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True
    ).strip()


def _checkout(root, files, origin):
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Synthetic CI")
    _git(root, "config", "user.email", "synthetic-ci@example.invalid")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    _git(root, "remote", "add", "origin", origin)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def sandbox(tmp_path):
    control = tmp_path / "control"
    product = tmp_path / "product"
    csha = _checkout(
        control, {"PROJECTS.yaml": REGISTRY},
        "https://github.com/ChoiWonJae850610/DEV-CONTROL.git",
    )
    psha = _checkout(
        product, {WORKFLOW: OLD}, "https://github.com/" + REPO + ".git",
    )
    request = SignedWorkflowSelection(
        project="FUTURE", repository=REPO, branch="dev-next",
        workflow_path=WORKFLOW, control_sha=csha, product_sha=psha,
        previous_sha256=hashlib.sha256(OLD.encode()).hexdigest(),
        proposed_sha256=hashlib.sha256(NEW.encode()).hexdigest(),
        expires_ms=1_800_000_100_000, nonce="f" * 32,
    )
    return control, product, request


class FakeApi:
    def __init__(self, request, *, open_pr=False, changed_head=False):
        self.request = request
        self.operations = []
        self.open_pr = open_pr
        self.changed_head = changed_head
        self.created_commit = "e" * 40
        self.ref_created = False

    def get(self, path, *, missing_ok=False):
        self.operations.append(("GET", path))
        if "/branches/dev-next" in path:
            return {"commit": {"sha": "b" * 40 if self.changed_head
                    else self.request.product_sha}}
        if "/pulls?state=open&base=" in path:
            return [{"number": 141}] if self.open_pr else []
        if "/git/ref/heads/lean-ci/" in path:
            assert missing_ok
            return None
        if "/git/commits/" in path:
            return {"tree": {"sha": "a" * 40}}
        if "/pulls/101/files?" in path:
            return [{"filename": WORKFLOW, "status": "modified"}]
        raise AssertionError("unexpected API GET: " + path)

    def post(self, path, payload):
        self.operations.append(("POST", path))
        if path.endswith("/git/blobs"):
            assert payload["encoding"] == "utf-8" and payload["content"] == NEW
            return {"sha": "c" * 40}
        if path.endswith("/git/trees"):
            assert len(payload["tree"]) == 1
            assert payload["tree"][0]["path"] == WORKFLOW
            return {"sha": "d" * 40}
        if path.endswith("/git/commits"):
            assert payload["parents"] == [self.request.product_sha]
            return {"sha": self.created_commit}
        if path.endswith("/git/refs"):
            self.ref_created = True
            assert payload["ref"].startswith("refs/heads/lean-ci/future-")
            return {"ref": payload["ref"]}
        if path.endswith("/pulls"):
            assert payload["draft"] is True
            assert payload["base"] == "dev-next"
            assert payload["head"].startswith("lean-ci/future-")
            return {
                "number": 101, "state": "open", "draft": True,
                "head": {"sha": self.created_commit},
                "base": {"sha": self.request.product_sha, "ref": "dev-next"},
                "html_url": "https://github.com/" + REPO + "/pull/101",
            }
        raise AssertionError("unexpected API POST: " + path)


def _publish(sandbox, api=None, signed=None, selection=None, candidate=NEW):
    control, product, original = sandbox
    choice = selection or original
    remote = api or FakeApi(choice)
    return publish_lean_ci_pr(
        control_checkout=control, product_checkout=product,
        selection=choice,
        publish_mac=signed if signed is not None else sign_publish_claim(choice, KEY),
        approval_secret=KEY, candidate=candidate, api=remote,
        now_ms=1_800_000_000_000,
    )


def test_exact_owner_signature_publishes_one_draft_ci_pr_not_a_merge(sandbox):
    product = sandbox[1]
    head = _git(product, "rev-parse", "HEAD")
    api = FakeApi(sandbox[2])
    receipt = _publish(sandbox, api)
    assert receipt.status == "DRAFT_PR_CREATED_CI_NOT_VERIFIED"
    assert receipt.pr_number == 101
    assert receipt.workflow_path == WORKFLOW
    assert receipt.merged is False and receipt.canonical_ci_verified is False
    assert _git(product, "rev-parse", "HEAD") == head
    assert _git(product, "status", "--porcelain") == "M " + WORKFLOW
    assert [path for op, path in api.operations if op == "POST"] == [
        "repos/" + REPO + "/git/blobs",
        "repos/" + REPO + "/git/trees",
        "repos/" + REPO + "/git/commits",
        "repos/" + REPO + "/git/refs",
        "repos/" + REPO + "/pulls",
    ]


def test_stage_signature_cannot_authorize_publish(sandbox):
    stage_only = "0" * 64
    with pytest.raises(LeanCiPublicationError, match="attestation"):
        _publish(sandbox, signed=stage_only)
    assert _git(sandbox[1], "status", "--porcelain") == ""


def test_changed_or_expired_owner_scope_is_rejected_before_git_write(sandbox):
    s = sandbox[2]
    sig = sign_publish_claim(s, KEY)
    for changed in (
        replace(s, project="OTHER"), replace(s, workflow_path="other.yml"),
        replace(s, proposed_sha256="0" * 64),
        replace(s, expires_ms=1_799_999_999_999),
    ):
        with pytest.raises(LeanCiPublicationError):
            verify_publish_claim(
                changed, sig, KEY, now_ms=1_800_000_000_000
            )


@pytest.mark.parametrize(("open_pr", "changed_head"), [(True, False), (False, True)])
def test_existing_product_writer_or_changed_branch_prevents_pr(
    sandbox, open_pr, changed_head,
):
    api = FakeApi(sandbox[2], open_pr=open_pr, changed_head=changed_head)
    with pytest.raises(LeanCiPublicationError):
        _publish(sandbox, api)
    assert not [entry for entry in api.operations if entry[0] == "POST"]
    assert _git(sandbox[1], "status", "--porcelain") == ""


def test_changed_candidate_digest_prevents_remote_writes(sandbox):
    api = FakeApi(sandbox[2])
    with pytest.raises(LeanCiPublicationError, match="digest"):
        _publish(sandbox, api, candidate=NEW + "#extra\n")
    assert not [entry for entry in api.operations if entry[0] == "POST"]


def test_rejects_noncanonical_changes_and_origin_mismatch(sandbox):
    choice = replace(sandbox[2], workflow_path=".github/workflows/other.yml")
    with pytest.raises(LeanCiPublicationError, match="active Git registry"):
        _publish(sandbox, selection=choice)
    _git(sandbox[1], "remote", "set-url", "origin", "https://github.com/Wrong/Repo")
    with pytest.raises(LeanCiPublicationError, match="origin"):
        _publish(sandbox)


def test_another_source_pr_is_not_safely_overwritten(sandbox):
    api = FakeApi(sandbox[2], open_pr=True)
    with pytest.raises(LeanCiPublicationError, match="another product PR"):
        _publish(sandbox, api)
    assert api.ref_created is False


def test_owner_publish_reuses_existing_pat_without_new_activation_secrets():
    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/lean-ci-owner-publish.yml").read_text(
                    encoding="utf-8"
                )
    assert "github.actor == github.repository_owner" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "LEAN_CI_APPROVAL_SECRET: ${{ secrets.OWNER_GITHUB_TOKEN }}" in workflow
    assert "LEAN_CI_OWNER_PUBLISH_ENABLED" not in workflow
    assert "secrets.LEAN_CI_APPROVAL_SECRET" not in workflow
    assert "gh repo clone" in workflow
