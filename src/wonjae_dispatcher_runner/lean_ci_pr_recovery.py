"""One-shot Owner-signed recovery of an existing registered CI-only PR.

Never edits product source/test files, creates a second PR, or merges. Only the
original registered YAML in the exact already-open WAFL #88 may be replaced.
The trusted workflow independently waits for new PR-head CI before merge.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .lean_ci_maintenance import LeanCiMaintenanceError, registered_ci
from .lean_ci_owner_stage import (
    SignedWorkflowSelection,
    _payload,
    sign_workflow_selection,
    stage_workflow,
)
from .lean_ci_pr_publisher import (
    GithubApi,
    LeanCiPublicationError,
    LeanCiPublishReceipt,
    _obj,
    _open_prs,
    _remote_head,
    verify_publish_claim,
)

_DOMAIN = b"registered-lean-ci-wafl-88-recovery-v1\n"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TARGET_REPO = "ChoiWonJae850610/WAFL"
_TARGET_WORKFLOW = ".github/workflows/wafl-cloud-validation.yml"
_TARGET_PR = 88


@dataclass(frozen=True)
class ExactCiRecovery:
    pr_number: int
    pr_head_sha: str
    failed_run_id: int
    current_workflow_sha256: str


def _scope(scope: ExactCiRecovery, selection: SignedWorkflowSelection) -> bytes:
    if (
        selection.project != "WAFL"
        or selection.repository != _TARGET_REPO
        or selection.branch != "cloud-dev-v1"
        or selection.workflow_path != _TARGET_WORKFLOW
        or type(scope.pr_number) is not int or scope.pr_number != _TARGET_PR
        or not isinstance(scope.pr_head_sha, str)
        or not _SHA.fullmatch(scope.pr_head_sha)
        or type(scope.failed_run_id) is not int or scope.failed_run_id <= 0
        or not isinstance(scope.current_workflow_sha256, str)
        or not _DIGEST.fullmatch(scope.current_workflow_sha256)
        or scope.current_workflow_sha256 in (
            selection.previous_sha256, selection.proposed_sha256
        )
    ):
        raise LeanCiPublicationError("unsupported or ambiguous exact CI recovery scope")
    return _DOMAIN + _payload(selection) + b"\n" + "\n".join((
        str(scope.pr_number), scope.pr_head_sha,
        str(scope.failed_run_id), scope.current_workflow_sha256,
    )).encode("ascii")


def sign_recovery_claim(
    selection: SignedWorkflowSelection, scope: ExactCiRecovery, secret: str,
) -> str:
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise LeanCiPublicationError("existing Owner signing credential unavailable")
    return hmac.new(
        secret.encode("utf-8"), _scope(scope, selection), hashlib.sha256,
    ).hexdigest()


def verify_recovery_claim(
    selection: SignedWorkflowSelection, scope: ExactCiRecovery, mac: str,
    secret: str, *, now_ms: int | None = None,
) -> None:
    # Reuse the EXACT expiry and nonce already signed for the normal rollout.
    clock = int(time.time() * 1000) if now_ms is None else now_ms
    if not isinstance(clock, int) or clock < 1 or not (
        clock <= selection.expires_ms <= clock + 600_000
    ):
        raise LeanCiPublicationError("exact recovery request expired")
    if not isinstance(mac, str) or not _DIGEST.fullmatch(mac):
        raise LeanCiPublicationError("missing signed recovery authorization")
    correct = sign_recovery_claim(selection, scope, secret)
    if not hmac.compare_digest(mac, correct):
        raise LeanCiPublicationError("existing CI PR recovery signature mismatch")


def _verified_existing(
    api: GithubApi, selection: SignedWorkflowSelection,
    scope: ExactCiRecovery, validation_name: str,
) -> str:
    root = "repos/" + selection.repository + "/"
    if _remote_head(api, selection.repository, selection.branch) != selection.product_sha:
        raise LeanCiPublicationError("registered product source branch moved")
    prs = _open_prs(api, selection.repository, selection.branch)
    if len(prs) != 1 or _obj(prs[0], "only open PR").get("number") != scope.pr_number:
        raise LeanCiPublicationError("another product source writer is present")
    pr = _obj(api.get(root + "pulls/" + str(scope.pr_number)), "existing CI PR")
    head = _obj(pr.get("head"), "CI PR head")
    base = _obj(pr.get("base"), "CI PR base")
    expected_branch_prefix = "lean-ci/wafl-" + selection.product_sha[:12] + "-"
    branch = head.get("ref")
    if not (
        pr.get("number") == scope.pr_number
        and pr.get("state") == "open"
        and pr.get("merged") is False
        and pr.get("draft") is False
        and pr.get("html_url") == "https://github.com/" + selection.repository
        + "/pull/" + str(scope.pr_number)
        and head.get("sha") == scope.pr_head_sha
        and head.get("repo", {}).get("full_name") == selection.repository
        and isinstance(branch, str)
        and branch.startswith(expected_branch_prefix)
        and re.fullmatch(r"lean-ci/wafl-[a-f0-9]{12}-[a-f0-9]{12}", branch)
        and base.get("ref") == selection.branch
        and base.get("sha") == selection.product_sha
        and base.get("repo", {}).get("full_name") == selection.repository
    ):
        raise LeanCiPublicationError("existing PR identity or head changed")
    body = pr.get("body")
    for expected in (
        "- Control SHA: " + selection.control_sha,
        "- Product base SHA: " + selection.product_sha,
        "- Workflow: " + selection.workflow_path,
        "- Old SHA256: " + selection.previous_sha256,
        "- New SHA256: " + scope.current_workflow_sha256,
    ):
        if not isinstance(body, str) or expected not in body:
            raise LeanCiPublicationError("existing PR signed history is not recognized")
    changed = api.get(root + "pulls/" + str(scope.pr_number) + "/files?per_page=100")
    if not isinstance(changed, list) or len(changed) != 1 or (
        _obj(changed[0], "CI PR file").get("filename") != selection.workflow_path
        or changed[0].get("status") != "modified"
    ):
        raise LeanCiPublicationError("existing PR includes other product paths")
    run = _obj(api.get(root + "actions/runs/" + str(scope.failed_run_id)), "failed CI run")
    if not (
        run.get("id") == scope.failed_run_id
        and run.get("head_sha") == scope.pr_head_sha
        and run.get("name") == validation_name
        and run.get("path", "").split("@", 1)[0] == selection.workflow_path
        and run.get("event") == "pull_request"
        and run.get("status") == "completed"
        and run.get("conclusion") == "failure"
        and _obj(run.get("repository"), "run repository").get("full_name")
        == selection.repository
    ):
        raise LeanCiPublicationError("no exact failing canonical CI evidence")
    old = _obj(api.get(root + "contents/" + selection.workflow_path
                       + "?ref=" + scope.pr_head_sha), "existing YAML")
    try:
        binary = base64.b64decode(old.get("content", ""), validate=False)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise LeanCiPublicationError("old PR workflow unavailable") from exc
    if old.get("encoding") != "base64" or not binary or len(binary) > 28_000:
        raise LeanCiPublicationError("old PR workflow content is invalid")
    if hashlib.sha256(binary).hexdigest() != scope.current_workflow_sha256:
        raise LeanCiPublicationError("old PR workflow digest changed")
    branch_ref = _obj(api.get(root + "git/ref/heads/" + branch), "CI PR branch ref")
    if _obj(branch_ref.get("object"), "CI PR ref object").get("sha") != scope.pr_head_sha:
        raise LeanCiPublicationError("CI PR branch not at reviewed commit")
    return branch


def recover_one_registered_ci_pr(
    *,
    control_checkout: Path,
    product_checkout: Path,
    selection: SignedWorkflowSelection,
    publish_mac: str,
    recovery: ExactCiRecovery,
    recovery_mac: str,
    approval_secret: str,
    candidate: str,
    api: GithubApi,
    now_ms: int | None = None,
) -> LeanCiPublishReceipt:
    """Update a single exact CI YAML within WAFL #88; no merge or PASS."""
    verify_publish_claim(selection, publish_mac, approval_secret, now_ms=now_ms)
    verify_recovery_claim(
        selection, recovery, recovery_mac, approval_secret, now_ms=now_ms,
    )
    registered = registered_ci(control_checkout / "PROJECTS.yaml")
    route = registered.get(selection.project)
    if route is None or route.repository != selection.repository or (
        route.validation_path != selection.workflow_path
    ):
        raise LeanCiPublicationError("current control registry does not admit recovery")
    branch = _verified_existing(api, selection, recovery, route.validation_name)
    # Reuse the EXACT original baseline, workflow structure, origin, clean
    # checkout and SHA/digest gate from the existing one-file publisher.
    try:
        stage = stage_workflow(
            control_checkout=control_checkout, product_checkout=product_checkout,
            selection=selection,
            approval_mac=sign_workflow_selection(selection, approval_secret),
            approval_secret=approval_secret, candidate=candidate, now_ms=now_ms,
        )
    except LeanCiMaintenanceError as exc:
        raise LeanCiPublicationError(str(exc)) from exc
    if stage.changed_paths != (selection.workflow_path,) or stage.published or stage.ci_started:
        raise LeanCiPublicationError("recovery cannot change any extra source file")
    if _verified_existing(api, selection, recovery, route.validation_name) != branch:
        raise LeanCiPublicationError("existing CI writer changed while staging")

    root = "repos/" + selection.repository + "/"
    old_commit = _obj(api.get(root + "git/commits/" + recovery.pr_head_sha), "PR commit")
    old_tree = _obj(old_commit.get("tree"), "PR commit tree").get("sha")
    parents = old_commit.get("parents")
    if not _SHA.fullmatch(old_tree or "") or (
        not isinstance(parents, list) or len(parents) != 1
        or _obj(parents[0], "prior commit parent").get("sha") != selection.product_sha
    ):
        raise LeanCiPublicationError("existing PR is not a single direct CI-only change")
    blob = _obj(api.post(root + "git/blobs", {
        "content": candidate, "encoding": "utf-8",
    }), "recovery blob").get("sha")
    if not isinstance(blob, str) or not _SHA.fullmatch(blob):
        raise LeanCiPublicationError("recovery blob SHA invalid")
    tree = _obj(api.post(root + "git/trees", {
        "base_tree": old_tree,
        "tree": [{"path": selection.workflow_path, "mode": "100644",
                  "type": "blob", "sha": blob}],
    }), "recovery tree").get("sha")
    if not isinstance(tree, str) or not _SHA.fullmatch(tree):
        raise LeanCiPublicationError("recovery tree SHA invalid")
    commit = _obj(api.post(root + "git/commits", {
        "message": "ci: avoid obsolete full-suite assertion in WAFL CI maintenance",
        "tree": tree, "parents": [recovery.pr_head_sha],
    }), "recovery commit").get("sha")
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        raise LeanCiPublicationError("recovery commit SHA invalid")
    if _verified_existing(api, selection, recovery, route.validation_name) != branch:
        raise LeanCiPublicationError("existing CI writer changed before publication")
    # No force push. GitHub rejects the ref update unless the exact direct
    # child of the prior PR head is a fast-forward.
    api.patch(root + "git/refs/heads/" + branch, {"sha": commit, "force": False})
    for attempt in range(12):
        observed = _obj(api.get(root + "pulls/" + str(recovery.pr_number)), "PR readback")
        if (
            observed.get("state") == "open"
            and observed.get("merged") is False
            and _obj(observed.get("head"), "PR head readback").get("sha") == commit
            and _obj(observed.get("base"), "PR base readback").get("sha")
            == selection.product_sha
        ):
            return LeanCiPublishReceipt(
                project=selection.project, repository=selection.repository,
                branch=branch, source_sha=selection.product_sha,
                workflow_path=selection.workflow_path,
                proposal_sha256=selection.proposed_sha256,
                pr_number=recovery.pr_number, pr_head_sha=commit,
                pr_url="https://github.com/" + selection.repository
                + "/pull/" + str(recovery.pr_number),
                status="EXISTING_CI_PR_REPAIRED_VALIDATION_PENDING",
            )
        if attempt < 11:
            time.sleep(5)
    raise LeanCiPublicationError("exact updated PR HEAD readback not yet consistent")
