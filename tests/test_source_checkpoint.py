"""Synthetic published repairs, real Git ancestry, API lag and terminal races."""

import subprocess
from copy import deepcopy

import pytest
from test_source_terminal import GitHub, Mail, reconcile

from wonjae_dispatcher_runner import source_checkpoint as cp
from wonjae_dispatcher_runner.source_pr_lifecycle import SourceIdentity, parse_fields
from wonjae_dispatcher_runner.source_terminal import SourceTerminalError


class Clock:
    def __init__(self):
        self.now, self.waits = 0, []

    def read(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture
def published(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, text=True, capture_output=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    heads = []
    for index in range(4):
        (tmp_path / "source.txt").write_text(f"source {index}\n")
        git("add", ".")
        git("commit", "-qm", f"synthetic {index}")
        heads.append(git("rev-parse", "HEAD"))
    github = GitHub()
    github.binding["last_pr_head"] = heads[0]
    github.issue["comments"][1]["body"] = (
        cp.CHECKPOINT_HEADING
        + "\n\n"
        + "\n".join(f"- {key}: `{value}`" for key, value in github.binding.items())
    )
    github.pr["headRefOid"] = heads[0]
    github.remote_head, github.reads, github.sequence = heads[0], [], []
    original = github.get_pr

    def read_pr(*args, **kwargs):
        value = original(*args, **kwargs)
        if github.sequence:
            value["headRefOid"] = github.sequence.pop(0)
        github.reads.append(value["headRefOid"])
        assert 0 < kwargs.get("timeout_seconds", 45) <= 45
        return value

    github.get_pr = read_pr
    github.get_product_commit = lambda repository, sha: {
        "sha": sha,
        "parents": [
            {"sha": value} for value in git("rev-list", "--parents", "-n", "1", sha).split()[1:]
        ],
    }
    github.get_product_branch_head = lambda *args: github.remote_head
    clock = Clock()
    monkeypatch.setattr(cp.time, "monotonic", clock.read)
    monkeypatch.setattr(cp.time, "sleep", clock.sleep)
    identity = SourceIdentity(
        "CLASSMO",
        github.record["repository"],
        "cloud-dev-v1",
        github.record["task_id"],
        2,
        github.binding["control_sha"],
        github.record["source_base_sha"],
        "101",
    )
    fields = {key: str(value) for key, value in github.binding.items()}
    return github, identity, fields, heads, clock, git


def transition(published, index=1):
    github, identity, fields, heads, clock, git = published
    new_fields = {**fields, "last_pr_head": heads[index]}
    cp.refresh_checkpoint(github, identity, new_fields, prepare=True, local_parent=heads[index - 1])
    github.remote_head = github.pr["headRefOid"] = heads[index]
    return new_fields


def test_a_old_old_new_converges_and_updates_same_comment(published):
    github, identity, _, heads, clock, _ = published
    fields = transition(published)
    github.sequence = [heads[0], heads[0], heads[1]]
    cp.refresh_checkpoint(github, identity, fields)
    assert github.reads[-4:] == [heads[0], heads[0], heads[1], heads[1]]
    assert clock.waits == [5, 5]
    comments = [c for c in github.issue["comments"] if cp.CHECKPOINT_HEADING in c["body"]]
    assert len(comments) == 1 and comments[0]["id"] == 2
    final = parse_fields(comments[0]["body"])
    assert final["last_pr_head"] == heads[1] and final["repair_commits"] == "1"
    assert "pending_repair_head" not in final
    before = list(github.mutations)
    cp.refresh_checkpoint(github, identity, fields)
    assert github.mutations == before  # repeated B is idempotent


def test_second_repair_and_third_rejected(published):
    github, identity, _, heads, _, _ = published
    fields = transition(published)
    cp.refresh_checkpoint(github, identity, fields)
    fields["last_pr_head"] = heads[2]
    cp.refresh_checkpoint(github, identity, fields, prepare=True, local_parent=heads[1])
    github.pr["headRefOid"] = github.remote_head = heads[2]
    cp.refresh_checkpoint(github, identity, fields)
    assert parse_fields(github.issue["comments"][1]["body"])["repair_commits"] == "2"
    fields["last_pr_head"] = heads[3]
    with pytest.raises(cp.SourceCheckpointError, match="budget"):
        cp.refresh_checkpoint(github, identity, fields, prepare=True, local_parent=heads[2])


@pytest.mark.parametrize("still_stale", [False, True])
def test_d_timeout_precise_evidence_then_exact_terminal_cleanup(published, still_stale):
    github, identity, _, heads, clock, _ = published
    fields = transition(published)
    github.pr["headRefOid"] = heads[0]
    with pytest.raises(cp.SourceCheckpointError, match="PR_HEAD_PROPAGATION_TIMEOUT") as exc:
        cp.refresh_checkpoint(github, identity, fields)
    assert heads[0] in str(exc.value) and heads[1] in str(exc.value)
    assert clock.now == 90 and len(clock.waits) == 18
    assert parse_fields(github.issue["comments"][1]["body"])["last_pr_head"] == heads[0]
    if not still_stale:
        github.pr["headRefOid"] = heads[1]  # visibility returns after the terminal boundary
    mail = Mail()
    assert reconcile(github, mail)["result"] == "CANCELLED"
    assert github.pr["state"] == github.issue["state"] == "CLOSED"
    assert github.pr["headRefOid"] == heads[0 if still_stale else 1]
    assert github.remote_head == heads[1] and github.pr["mergedAt"] is None


@pytest.mark.parametrize(
    "mismatch", ["number", "closed", "merged", "branch", "base", "repository", "metadata", "head"]
)
def test_e_only_old_head_lag_is_retryable(published, mismatch):
    github, identity, _, heads, clock, _ = published
    fields = transition(published)
    if mismatch == "number":
        github.pr["number"] = 8
    elif mismatch == "closed":
        github.pr["state"] = "CLOSED"
    elif mismatch == "merged":
        github.pr["mergedAt"] = "2026-01-01"
    elif mismatch == "branch":
        github.pr["headRefName"] = "job/unrelated"
    elif mismatch == "base":
        github.pr["baseRefName"] = "production"
    elif mismatch == "repository":
        github.pr["headRepository"]["nameWithOwner"] = "owner/unrelated"
    elif mismatch == "metadata":
        github.pr["body"] = github.pr["body"].replace(github.binding["control_sha"], "e" * 40)
    else:
        github.pr["headRefOid"] = heads[3]
    before = list(github.mutations)
    with pytest.raises(cp.SourceCheckpointError):
        cp.refresh_checkpoint(github, identity, fields)
    assert clock.waits == [] and github.mutations == before


@pytest.mark.parametrize(
    "boundary", ["before_push", "after_push", "after_readback", "after_readback_stale"]
)
def test_g_active_transition_not_terminal_but_real_cancel_cleans_exact_pr(published, boundary):
    github, identity, _, heads, _, _ = published
    fields = transition(published)
    if boundary == "before_push":
        github.pr["headRefOid"] = github.remote_head = heads[0]
    elif boundary.startswith("after_readback"):
        cp.refresh_checkpoint(github, identity, fields)
        if boundary == "after_readback_stale":
            github.pr["headRefOid"] = heads[0]
    github.run["status"] = github.jobs[0]["status"] = "in_progress"
    before = list(github.mutations)
    with pytest.raises(SourceTerminalError, match="status"):
        reconcile(github, Mail())
    assert github.mutations == before and github.pr["state"] == "OPEN"
    github.run["status"] = github.jobs[0]["status"] = "completed"
    assert reconcile(github, Mail())["result"] == "CANCELLED"
    assert github.pr["state"] == "CLOSED" and github.pr["mergedAt"] is None


def test_unknown_published_head_and_non_descendant_never_adopted(published):
    github, identity, _, heads, _, _ = published
    fields = transition(published)
    github.remote_head = heads[3]
    with pytest.raises(SourceTerminalError, match="publication intent"):
        reconcile(github, Mail())
    github.get_product_commit = lambda *args: {"sha": heads[1], "parents": [{"sha": heads[3]}]}
    with pytest.raises(cp.SourceCheckpointError, match="descendant"):
        cp.refresh_checkpoint(github, identity, fields)
    assert github.pr["state"] == "OPEN"


def test_checkpoint_readback_must_confirm_same_comment(published):
    github, identity, fields, heads, _, _ = published
    transition(published)
    fields["last_pr_head"] = heads[1]
    github.update_comment = lambda *args: None
    with pytest.raises(cp.SourceCheckpointError, match="write/readback"):
        cp.refresh_checkpoint(github, identity, fields)


def test_h_initial_exact_head_has_no_retry_delay(published):
    github, identity, fields, _, clock, _ = published
    github.issue["comments"] = github.issue["comments"][:1]
    cp.refresh_checkpoint(github, identity, fields)
    assert clock.waits == [] and len(github.reads) == 2


def test_immutable_checkpoint_binding_cannot_change(published):
    github, identity, fields, _, _, _ = published
    fields = deepcopy(fields)
    fields["source_sha"] = "e" * 40
    with pytest.raises(cp.SourceCheckpointError, match="immutable"):
        cp.refresh_checkpoint(github, identity, fields)
    assert github.mutations == []
