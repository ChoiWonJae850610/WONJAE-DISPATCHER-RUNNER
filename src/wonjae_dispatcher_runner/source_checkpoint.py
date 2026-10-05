"""Bounded exact PR-head publication; never validates, merges or starts a task."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .guards import require_sha
from .source_pr_lifecycle import SourceIdentity, parse_fields, parse_pr_metadata

CHECKPOINT_HEADING = "## Dispatcher v2 source run identity"
HEAD_READBACK_SECONDS = 90
HEAD_READBACK_INTERVAL = 5


class SourceCheckpointError(RuntimeError):
    pass


def verify_pr(
    pr: dict,
    identity: SourceIdentity,
    number: int,
    revision: str,
    *,
    terminal: bool = False,
) -> None:
    expected = {
        "project": identity.project,
        "task_id": identity.task_id,
        "attempt": str(identity.attempt),
        "revision": revision,
        "repository": identity.repository,
        "target_branch": identity.target_branch,
        "source_base_sha": identity.source_sha,
        "control_sha": identity.control_sha,
    }
    metadata = parse_pr_metadata(str(pr.get("body") or ""))
    if (
        pr.get("number") != number
        or pr.get("mergedAt")
        or pr.get("state") not in ({"OPEN", "CLOSED"} if terminal else {"OPEN"})
        or pr.get("baseRefName") != identity.target_branch
        or pr.get("headRefName") != identity.expected_branch
        or (pr.get("headRepository") or {}).get("nameWithOwner") != identity.repository
        or any(metadata.get(key) != value for key, value in expected.items())
    ):
        raise SourceCheckpointError(
            "checkpoint PR identity/state/metadata/branch/base/repository mismatch"
        )


def read_exact_head(
    client: Any,
    identity: SourceIdentity,
    number: int,
    revision: str,
    head: str,
    previous: str = "",
    *,
    terminal: bool = False,
    clock=None,
    sleep=None,
    metadata_sha256: str = "",
    deadline: float | None = None,
) -> dict:
    """Retry only the previous exact checkpoint head on the same authorized PR.

    Per-request timeout fits inside the one wall-clock deadline. API errors and
    all other heads or identity changes fail closed; time never implies success.
    """
    require_sha(head)
    if previous:
        require_sha(previous)
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    deadline = deadline if deadline is not None else clock() + HEAD_READBACK_SECONDS
    observed = ""
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise SourceCheckpointError(
                f"PR_HEAD_PROPAGATION_TIMEOUT expected={head} observed={observed} pr={number}"
            )
        pr = client.get_pr(identity.repository, number, timeout_seconds=min(15, remaining))
        verify_pr(pr, identity, number, revision, terminal=terminal)
        if metadata_sha256 and metadata_digest(pr) != metadata_sha256:
            raise SourceCheckpointError("checkpoint PR metadata changed during repair publication")
        observed = str(pr.get("headRefOid") or "")
        if observed == head:
            return pr
        if not previous or previous == head or observed != previous:
            raise SourceCheckpointError(
                f"checkpoint PR unexpected head expected={head} observed={observed} pr={number}"
            )
        sleep(min(HEAD_READBACK_INTERVAL, max(0, deadline - clock())))


def verify_repair_commit(client: Any, identity: SourceIdentity, old: str, new: str) -> None:
    """The published repair must be one direct, non-merge child of the prior head."""
    require_sha(old)
    require_sha(new)
    commit = client.get_product_commit(identity.repository, new)
    if commit.get("sha") != new or [parent.get("sha") for parent in commit.get("parents", [])] != [
        old
    ]:
        raise SourceCheckpointError(
            "repair head is not one direct descendant of checkpoint; no force push"
        )


def find_checkpoint(client: Any, wake: int, run_id: str) -> dict | None:
    comments = [
        comment
        for comment in client.get_issue(wake).get("comments", [])
        if (comment.get("user") or comment.get("author") or {}).get("login")
        in {"github-actions", "github-actions[bot]"}
        and CHECKPOINT_HEADING in str(comment.get("body") or "")
        and parse_fields(comment["body"]).get("runner_run_id") == run_id
    ]
    if len(comments) > 1:
        raise SourceCheckpointError("duplicate source run checkpoints")
    return comments[0] if comments else None


def metadata_digest(pr: dict) -> str:
    fields = parse_pr_metadata(str(pr.get("body") or ""))
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def write_checkpoint(client: Any, fields: dict[str, str], prior: dict | None) -> None:
    wake, run_id = int(fields["wake_issue"]), fields["runner_run_id"]
    body = (
        CHECKPOINT_HEADING
        + "\n\n"
        + "\n".join(f"- {key}: `{value}`" for key, value in sorted(fields.items()))
        + "\n"
    )
    if prior:
        number = prior["id"]
        if prior["body"] != body:
            client.update_comment(number, body)
    else:
        number = client.add_comment(wake, body)
    readback = find_checkpoint(client, wake, run_id)
    if not readback or readback.get("id") != number or readback.get("body") != body:
        raise SourceCheckpointError("source checkpoint write/readback mismatch")


def refresh_checkpoint(
    client: Any,
    identity: SourceIdentity,
    fields: dict[str, str],
    *,
    prepare: bool = False,
    local_parent: str = "",
    clock=None,
    sleep=None,
) -> None:
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    deadline = clock() + HEAD_READBACK_SECONDS
    fields = {
        **fields,
        "product_job_branch": identity.expected_branch,
        "product_head_repository": identity.repository,
    }
    head = fields["last_pr_head"]
    require_sha(head)
    number, revision = int(fields["product_pr"]), fields["revision"]
    prior = find_checkpoint(client, int(fields["wake_issue"]), identity.runner_run_id)
    old = parse_fields(prior["body"]) if prior else {}
    optional_legacy = {
        "product_job_branch": identity.expected_branch,
        "product_head_repository": identity.repository,
    }
    if prior and any(
        old.get(key, optional_legacy.get(key)) != value
        for key, value in fields.items()
        if key != "last_pr_head"
    ):
        raise SourceCheckpointError("source checkpoint immutable binding mismatch")
    previous = old.get("last_pr_head", "")
    count = old.get("repair_commits", "0")
    if count not in {"0", "1", "2"}:
        raise SourceCheckpointError("invalid checkpoint repair count")
    if prepare:
        if (
            not prior
            or head == previous
            or local_parent != previous
            or count == "2"
            or old.get("pending_repair_head", head) != head
        ):
            raise SourceCheckpointError("repair publication intent/parent/budget mismatch")
        # Persist the exact trusted local child before push. A cancellation between
        # push and readback can then close only old or pending, never an arbitrary head.
        read_exact_head(
            client,
            identity,
            number,
            revision,
            previous,
            clock=clock,
            sleep=sleep,
            metadata_sha256=old.get("metadata_sha256", ""),
        )
        write_checkpoint(
            client,
            {
                **old,
                **optional_legacy,
                "checkpoint_state": "PUBLISHING_REPAIR",
                "pending_repair_head": head,
            },
            prior,
        )
        return
    if previous and previous != head:
        if count == "2" or old.get("pending_repair_head", head) != head:
            raise SourceCheckpointError("repair publication intent/budget mismatch")
        verify_repair_commit(client, identity, previous, head)
        # Also supports an older trusted caller that did not prepare before push.
        # Retain the target before bounded waiting so a timeout has exact cleanup authority.
        if not old.get("pending_repair_head"):
            observed = client.get_pr(identity.repository, number, timeout_seconds=15)
            verify_pr(observed, identity, number, revision)
            if observed.get("headRefOid") not in {previous, head}:
                raise SourceCheckpointError("unprepared publication has unexpected PR head")
            old.update(checkpoint_state="PUBLISHING_REPAIR", pending_repair_head=head)
            write_checkpoint(client, old, prior)
            prior = find_checkpoint(client, int(fields["wake_issue"]), identity.runner_run_id)
        pr = read_exact_head(
            client,
            identity,
            number,
            revision,
            head,
            previous,
            clock=clock,
            sleep=sleep,
            metadata_sha256=old.get("metadata_sha256", ""),
            deadline=deadline,
        )
        fields = {**fields, "previous_pr_head": previous, "repair_commits": str(int(count) + 1)}
    else:
        if old.get("pending_repair_head"):
            raise SourceCheckpointError(
                "pending repair cannot be discarded by stale checkpoint call"
            )
        pr = read_exact_head(
            client,
            identity,
            number,
            revision,
            head,
            clock=clock,
            sleep=sleep,
            metadata_sha256=old.get("metadata_sha256", ""),
        )
        fields = {**fields, "repair_commits": count}
        if old.get("previous_pr_head"):
            fields["previous_pr_head"] = old["previous_pr_head"]
    write_checkpoint(
        client,
        {**fields, "checkpoint_state": "READY", "metadata_sha256": metadata_digest(pr)},
        prior,
    )
    read_exact_head(
        client,
        identity,
        number,
        revision,
        head,
        previous,
        metadata_sha256=metadata_digest(pr),
        deadline=deadline,
        clock=clock,
        sleep=sleep,
    )


def terminal_repair_head(
    client: Any,
    identity: SourceIdentity,
    checkpoint: dict[str, str],
    revision: str,
) -> str:
    """Called only after terminal run AND product-job authority is established."""
    old, pending = checkpoint["last_pr_head"], checkpoint.get("pending_repair_head", "")
    if not pending:
        return old
    if checkpoint.get("checkpoint_state") != "PUBLISHING_REPAIR" or checkpoint.get(
        "repair_commits", "0"
    ) not in {"0", "1"}:
        raise SourceCheckpointError("invalid repair transition state")
    require_sha(old)
    require_sha(pending)
    branch_head = client.get_product_branch_head(identity.repository, identity.expected_branch)
    if branch_head not in {old, pending}:
        raise SourceCheckpointError(
            "terminal repair branch head is outside trusted publication intent"
        )
    if branch_head == pending:
        verify_repair_commit(client, identity, old, pending)
    # Terminal cleanup does not grant validation or integration authority. Once
    # the job really ended, exact PR identity plus the published authorized branch
    # allows closure even when the PR API still shows its known previous head.
    pr = client.get_pr(identity.repository, int(checkpoint["product_pr"]))
    verify_pr(pr, identity, int(checkpoint["product_pr"]), revision, terminal=True)
    if pr.get("headRefOid") not in {old, branch_head} or (
        checkpoint.get("metadata_sha256") and metadata_digest(pr) != checkpoint["metadata_sha256"]
    ):
        raise SourceCheckpointError(
            "terminal PR head/metadata differs from trusted repair transition"
        )
    return branch_head
