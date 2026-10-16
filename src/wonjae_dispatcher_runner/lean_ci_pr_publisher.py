"""One-workflow CI-only PR publisher, exclusively for signed Owner runs.

This is NOT reachable from the ordinary Direct Worker. Never execute in PR
validation or from untrusted GitHub content. Publication is gated by an
authenticated trusted workflow and purpose-bound HMAC. This module itself
never merges or declares CI PASS; the signed trusted Runner may then complete
exact PR-head and integrated-SHA checks before acknowledging the rollout.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .lean_ci_maintenance import LeanCiMaintenanceError
from .lean_ci_owner_stage import (
    LeanCiOwnerStageError,
    SignedWorkflowSelection,
    _payload,
    sign_workflow_selection,
    stage_workflow,
)

_PREFIX = b"registered-lean-ci-pr-publish-v1\n"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_EXPIRE_WINDOW_MS = 10 * 60 * 1000
_MAX_REPO_DATA = 500_000


class LeanCiPublicationError(LeanCiOwnerStageError):
    """A scope, identity, collision, API or publish-effect guard failed."""


class GithubApi(Protocol):
    def get(self, path: str, *, missing_ok: bool = False) -> object: ...
    def post(self, path: str, payload: dict) -> object: ...


class GitHubRest:
    """Bound HTTPS GitHub API only; no arbitrary path or external host."""

    def __init__(self, repo: str, token: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise LeanCiPublicationError("invalid publishing repository")
        if not token:
            raise LeanCiPublicationError("missing existing Owner GitHub action token")
        self.repo = repo
        self._token = token

    def _request(self, method: str, path: str, data: dict | None = None,
                 missing_ok: bool = False) -> object:
        root = "repos/" + self.repo + "/"
        if not path.startswith(root) or any(
            x in path for x in ("\r", "\n", "\\", "..", "#")
        ):
            raise LeanCiPublicationError("GitHub API path outside registered repository")
        address = "https://api.github.com/" + path
        binary = json.dumps(data).encode("utf-8") if data is not None else None
        request = Request(address, method=method, data=binary, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self._token,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "trusted-lean-ci-owner-publisher",
        })
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read(_MAX_REPO_DATA + 1)
                if len(raw) > _MAX_REPO_DATA:
                    raise LeanCiPublicationError("GitHub response exceeded bounded size")
                return json.loads(raw)
        except HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            raise LeanCiPublicationError(
                "GitHub rejected exact publishing operation"
            ) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise LeanCiPublicationError(
                "GitHub exact publishing evidence unavailable"
            ) from None

    def get(self, path: str, *, missing_ok: bool = False) -> object:
        return self._request("GET", path, missing_ok=missing_ok)

    def post(self, path: str, payload: dict) -> object:
        return self._request("POST", path, data=payload)


@dataclass(frozen=True)
class LeanCiPublishReceipt:
    project: str
    repository: str
    branch: str
    source_sha: str
    workflow_path: str
    proposal_sha256: str
    pr_number: int
    pr_head_sha: str
    pr_url: str
    status: str = "PRODUCT_CI_PR_CREATED_VALIDATION_PENDING"
    merged: bool = False
    canonical_ci_verified: bool = False


def sign_publish_claim(selection: SignedWorkflowSelection, secret: str) -> str:
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise LeanCiPublicationError("dedicated Owner signing key is unavailable")
    try:
        return hmac.new(
            secret.encode("utf-8"), _PREFIX + _payload(selection), hashlib.sha256
        ).hexdigest()
    except LeanCiOwnerStageError as exc:
        raise LeanCiPublicationError("invalid signed publication scope") from exc


def verify_publish_claim(selection: SignedWorkflowSelection, mac: str,
                         secret: str, *, now_ms: int | None = None) -> None:
    clock = int(time.time() * 1000) if now_ms is None else now_ms
    if (not isinstance(clock, int) or clock < 1
            or selection.expires_ms < clock
            or selection.expires_ms > clock + _EXPIRE_WINDOW_MS):
        raise LeanCiPublicationError("product CI publishing Owner claim expired")
    if not isinstance(mac, str) or not re.fullmatch(r"[0-9a-f]{64}", mac):
        raise LeanCiPublicationError("invalid Owner publish attestation")
    expected = sign_publish_claim(selection, secret)
    if not hmac.compare_digest(mac, expected):
        raise LeanCiPublicationError("Owner attestation does not approve PR publishing")


def _obj(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise LeanCiPublicationError(label + " response is not a mapping")
    return value


def _remote_head(api: GithubApi, repository: str, branch: str) -> str:
    state = _obj(api.get(
        "repos/" + repository + "/branches/" + quote(branch, safe="/")
    ), "branch")
    sha = _obj(state.get("commit"), "branch commit").get("sha")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise LeanCiPublicationError("remote branch HEAD is invalid")
    return sha


def _open_prs(api: GithubApi, repository: str, branch: str) -> list:
    value = api.get("repos/" + repository + "/pulls?state=open&base="
                    + quote(branch, safe="") + "&per_page=100")
    if not isinstance(value, list) or len(value) >= 100:
        raise LeanCiPublicationError("product open PR inventory is ambiguous")
    return value


def publish_lean_ci_pr(
    *,
    control_checkout: Path,
    product_checkout: Path,
    selection: SignedWorkflowSelection,
    publish_mac: str,
    approval_secret: str,
    candidate: str,
    api: GithubApi,
    now_ms: int | None = None,
) -> LeanCiPublishReceipt:
    """Publish one registered CI-only PR; never merge or claim CI result here."""
    verify_publish_claim(selection, publish_mac, approval_secret, now_ms=now_ms)
    repository = selection.repository
    root = "repos/" + repository + "/"

    # Read the remote branch and pending writer inventory BEFORE any Git write.
    if _remote_head(api, repository, selection.branch) != selection.product_sha:
        raise LeanCiPublicationError("product active branch changed")
    if _open_prs(api, repository, selection.branch):
        raise LeanCiPublicationError("another product PR is open; preserve writer")
    branch = (
        "lean-ci/" + selection.project.lower() + "-"
        + selection.product_sha[:12] + "-" + selection.proposed_sha256[:12]
    )
    if api.get(root + "git/ref/heads/" + branch, missing_ok=True) is not None:
        raise LeanCiPublicationError("proposed lean CI branch already exists")

    # Existing stage verifier rechecks source, control, origin, exact hashes,
    # workflow permissions, triggers, YAML and single allowed changed path.
    try:
        stage = stage_workflow(
            control_checkout=control_checkout, product_checkout=product_checkout,
            selection=selection,
            approval_mac=sign_workflow_selection(selection, approval_secret),
            approval_secret=approval_secret, candidate=candidate, now_ms=now_ms,
        )
    except LeanCiMaintenanceError as exc:
        raise LeanCiPublicationError(str(exc)) from exc
    if (stage.changed_paths != (selection.workflow_path,)
            or stage.published or stage.ci_started):
        raise LeanCiPublicationError("staging result is not single-path only")

    # Reobserve before allocating a Git object. Collision is fail-closed.
    if _remote_head(api, repository, selection.branch) != selection.product_sha:
        raise LeanCiPublicationError("product branch moved after local stage")
    if _open_prs(api, repository, selection.branch):
        raise LeanCiPublicationError("another source writer is currently open")
    commit = _obj(api.get(root + "git/commits/" + selection.product_sha), "commit")
    tree = _obj(commit.get("tree"), "commit tree").get("sha")
    if not isinstance(tree, str) or not _SHA.fullmatch(tree):
        raise LeanCiPublicationError("exact source tree is missing")
    blob = _obj(api.post(root + "git/blobs", {
        "content": candidate, "encoding": "utf-8",
    }), "workflow blob").get("sha")
    if not isinstance(blob, str) or not _SHA.fullmatch(blob):
        raise LeanCiPublicationError("GitHub did not return a valid workflow blob")
    new_tree = _obj(api.post(root + "git/trees", {
        "base_tree": tree,
        "tree": [{"path": selection.workflow_path, "mode": "100644",
                  "type": "blob", "sha": blob}],
    }), "single-file tree").get("sha")
    if not isinstance(new_tree, str) or not _SHA.fullmatch(new_tree):
        raise LeanCiPublicationError("GitHub did not return a valid CI tree")
    new_commit = _obj(api.post(root + "git/commits", {
        "message": "ci: focus registered " + selection.project + " development checks",
        "tree": new_tree, "parents": [selection.product_sha],
    }), "CI change commit").get("sha")
    if not isinstance(new_commit, str) or not _SHA.fullmatch(new_commit):
        raise LeanCiPublicationError("GitHub CI change commit is invalid")
    # A branch ref is created only when the current target HEAD is unchanged.
    if (_remote_head(api, repository, selection.branch) != selection.product_sha
            or _open_prs(api, repository, selection.branch)):
        raise LeanCiPublicationError("product writer changed during preparation")
    api.post(root + "git/refs", {
        "ref": "refs/heads/" + branch, "sha": new_commit,
    })
    # Do not auto-clean or retry ambiguous writes. CI and integration belong to
    # separate bounded checks in the same authenticated trusted Runner job.
    result = _obj(api.post(root + "pulls", {
        "title": "ci: lean Development validation for " + selection.project,
        "body": (
            "Owner-reviewed, registered validation YAML only.\n\n"
            "- Control SHA: " + selection.control_sha + "\n"
            "- Product base SHA: " + selection.product_sha + "\n"
            "- Workflow: " + selection.workflow_path + "\n"
            "- Old SHA256: " + selection.previous_sha256 + "\n"
            "- New SHA256: " + selection.proposed_sha256 + "\n"
            "- Product canonical PR-head CI: **NOT VERIFIED**\n"
            "- Merge only after exact PR-head CI PASS in this Owner-signed run.\n"
            "- Integrated-SHA CI, Provider, Production: independently verified/not run.\n"
        ),
        "base": selection.branch, "head": branch, "draft": False,
        "maintainer_can_modify": False,
    }), "CI-only PR")
    number = result.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise LeanCiPublicationError("GitHub did not return a CI PR number")
    if (result.get("state") != "open" or result.get("draft") is not False
            or _obj(result.get("head"), "PR head").get("sha") != new_commit
            or _obj(result.get("base"), "PR base").get("sha") != selection.product_sha
            or _obj(result.get("base"), "PR base").get("ref") != selection.branch
            or result.get("html_url") !=
            "https://github.com/" + repository + "/pull/" + str(number)):
        raise LeanCiPublicationError("GitHub PR identity did not match reviewed scope")
    # The PR file inventory must independently contain exactly the one path.
    inventory = api.get(root + "pulls/" + str(number) + "/files?per_page=100")
    if not isinstance(inventory, list) or len(inventory) != 1:
        raise LeanCiPublicationError("CI PR contains unexpected files")
    changed = _obj(inventory[0], "PR file")
    if (changed.get("filename") != selection.workflow_path
            or changed.get("status") != "modified"):
        raise LeanCiPublicationError("CI PR modified a nonregistered path")
    return LeanCiPublishReceipt(
        project=selection.project,
        repository=repository,
        branch=branch,
        source_sha=selection.product_sha,
        workflow_path=selection.workflow_path,
        proposal_sha256=selection.proposed_sha256,
        pr_number=number, pr_head_sha=new_commit, pr_url=result["html_url"],
    )
