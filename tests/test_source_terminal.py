from copy import deepcopy

import pytest

from wonjae_dispatcher_runner.gmail_notifications import NotificationService, notification_key
from wonjae_dispatcher_runner.source_pr_lifecycle import parse_fields
from wonjae_dispatcher_runner.source_terminal import (
    CHECKPOINT_HEADING,
    TERMINAL_HEADING,
    FinalizerRequest,
    SourceTerminalError,
    reconcile_source_terminal,
)

OWNER = "owner"
REPOSITORY = "owner/runner"
TASK = "CLASSMO-TEST-001"
CONTROL, SOURCE, HEAD, RUNNER_HEAD = (letter * 40 for letter in "abcd")


def markdown(heading, fields):
    return heading + "\n\n" + "\n".join(f"- {key}: `{value}`" for key, value in fields.items())


class GitHub:
    def __init__(self, conclusion="cancelled", checkpoint=True):
        self.record = {
            "project": "CLASSMO",
            "task_id": TASK,
            "attempt": 2,
            "revision": 2,
            "repository": "owner/CLASSMO",
            "target_branch": "cloud-dev-v1",
            "source_base_sha": SOURCE,
            "profile": "standard",
        }
        self.run = {
            "id": 101,
            "status": "completed",
            "conclusion": conclusion,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": RUNNER_HEAD,
            "path": ".github/workflows/classmo-product-pilot.yml",
            "repository": {"full_name": REPOSITORY},
            "name": "dynamic run name is not workflow identity",
            "display_title": f"[PRODUCT-WAKE][DISPATCHER-V2] CLASSMO {TASK} {CONTROL} {SOURCE}",
        }
        self.jobs = [
            {
                "name": "classmo-product",
                "status": "completed",
                "conclusion": conclusion,
                "steps": [
                    {"name": "[06] STARTED · read back Draft product PR", "conclusion": "success"}
                ],
            }
        ]
        claim = {
            "project": "CLASSMO",
            "task_id": TASK,
            "control_sha": CONTROL,
            "source_sha": SOURCE,
            "wake_issue": 11,
            "workflow_file": "classmo-product-pilot.yml",
            "queue_state": "DISPATCHED",
        }
        self.binding = {
            "project": "CLASSMO",
            "task_id": TASK,
            "attempt": 2,
            "revision": 2,
            "control_sha": CONTROL,
            "source_sha": SOURCE,
            "runner_run_id": 101,
            "wake_issue": 11,
            "repository": "owner/CLASSMO",
            "target_branch": "cloud-dev-v1",
            "runner_head_sha": RUNNER_HEAD,
            "runner_workflow_path": self.run["path"],
            "product_pr": 7,
            "last_pr_head": HEAD,
        }
        self.issue = {
            "number": 11,
            "state": "OPEN",
            "author": {"login": OWNER},
            "title": self.run["display_title"],
            "comments": [
                self.comment(1, markdown("## Dispatcher v2 source queue evidence", claim))
            ],
        }
        if checkpoint:
            self.issue["comments"].append(
                self.comment(2, markdown(CHECKPOINT_HEADING, self.binding))
            )
        metadata = {**self.record, "control_sha": CONTROL}
        self.pr = {
            "number": 7,
            "state": "OPEN",
            "mergedAt": None,
            "headRefOid": HEAD,
            "headRefName": f"job/{TASK}-a2",
            "baseRefName": "cloud-dev-v1",
            "body": markdown("## Dispatcher v2 metadata", metadata),
        }
        self.mutations = []
        self.close_error = False

    @staticmethod
    def comment(number, body):
        return {"id": number, "body": body, "user": {"login": "github-actions[bot]"}}

    def get_run(self, number):
        return deepcopy(self.run)

    def get_jobs(self, number):
        return deepcopy(self.jobs)

    def get_issue(self, number):
        return deepcopy(self.issue)

    def get_pr(self, repository, number):
        assert repository == "owner/CLASSMO"
        return deepcopy(self.pr)

    def close_pr(self, repository, number):
        assert number == 7
        if self.close_error:
            raise RuntimeError("synthetic close failure")
        self.mutations.append("close_pr")
        self.pr["state"] = "CLOSED"

    def add_comment(self, number, body):
        self.mutations.append("add_comment")
        number = 99
        self.issue["comments"].append(self.comment(number, body))
        return number

    def update_comment(self, number, body):
        self.mutations.append("update_comment")
        next(comment for comment in self.issue["comments"] if comment["id"] == number)["body"] = (
            body
        )

    def close_issue(self, number):
        self.mutations.append("close_issue")
        self.issue["state"] = "CLOSED"


class Mail:
    def __init__(self):
        self.started = notification_key("CLASSMO", TASK, 2, "STARTED")
        self.unrelated = notification_key("WAFL", "WAFL-TEST-001", 2, "STARTED")
        self.messages = {self.started: {"INBOX"}, self.unrelated: {"INBOX"}}
        self.sent, self.trashed = [], []
        self.fail_readback = False

    def notification_exists(self, key):
        return key in self.messages

    def send(self, notification):
        self.sent.append(notification.key)
        self.messages[notification.key] = {"INBOX"}

    def wait_for_notification(self, key):
        return not self.fail_readback and key in self.messages

    def apply_label(self, key, label):
        self.messages[key].add(label)

    def trash_notification(self, key):
        if "TRASH" not in self.messages[key]:
            self.trashed.append(key)
            self.messages[key].discard("INBOX")
            self.messages[key].add("TRASH")
        return "TRASH" in self.messages[key] and "INBOX" not in self.messages[key]


def reconcile(github, mail, request=None):
    return reconcile_source_terminal(
        github,
        NotificationService(mail),
        request or FinalizerRequest(101, 11),
        github.record,
        REPOSITORY,
        OWNER,
    )


@pytest.mark.parametrize(
    "conclusion,result",
    [
        ("cancelled", "CANCELLED"),
        ("timed_out", "FAILED"),
        ("failure", "FAILED"),
        ("action_required", "MANUAL_REQUIRED"),
        ("startup_failure", "FAILED"),
    ],
)
def test_external_finalizer_closes_exact_orphan_and_delivers_result(conclusion, result):
    github, mail = GitHub(conclusion), Mail()
    outcome = reconcile(github, mail)
    assert outcome["result"] == result and outcome["sent"] is True
    assert github.pr["state"] == github.issue["state"] == "CLOSED"
    assert github.pr["mergedAt"] is None and github.pr["headRefOid"] == HEAD
    assert github.pr["headRefName"] == f"job/{TASK}-a2"
    evidence = [c for c in github.issue["comments"] if TERMINAL_HEADING in c["body"]]
    assert len(evidence) == 1
    fields = parse_fields(evidence[0]["body"])
    assert fields["recovery_state"] == "FINAL" and fields["revision"] == "2"
    assert fields["runner_run_id"] == "101" and fields["runner_conclusion"] == conclusion
    assert fields["runtime_device_physical"] == "NOT_INFERRED"
    assert mail.messages[mail.started] == {"TRASH"}
    assert mail.messages[mail.unrelated] == {"INBOX"}


def test_finalizer_replay_has_no_duplicate_mutations_or_result():
    github, mail = GitHub(), Mail()
    reconcile(github, mail)
    mutations = list(github.mutations)
    assert reconcile(github, mail)["sent"] is False
    assert github.mutations == mutations
    assert len(mail.sent) == len(mail.trashed) == 1


def test_existing_inline_terminal_is_reconciled_without_duplicate_evidence_or_mail():
    github, mail = GitHub(), Mail()
    reconcile(github, mail)
    terminal = github.issue["comments"][-1]
    terminal["body"] += "\n- inline_detail: `existing primary runner terminal checkpoint`"
    reconcile(github, mail)
    assert github.mutations.count("add_comment") == 1
    assert github.mutations.count("close_pr") == github.mutations.count("close_issue") == 1
    assert len(mail.sent) == 1


@pytest.mark.parametrize(
    "mismatch",
    [
        "pr",
        "control",
        "source",
        "attempt",
        "revision",
        "run_id",
        "workflow",
        "target",
        "head",
        "merged",
        "queue",
        "checkpoint",
        "product_job_success",
        "untrusted_checkpoint",
    ],
)
def test_identity_mismatch_refuses_all_mutation(mismatch):
    github, mail = GitHub(), Mail()
    request = FinalizerRequest(101, 11)
    if mismatch == "pr":
        request = FinalizerRequest(101, 11, 8)
    elif mismatch == "control":
        github.pr["body"] = github.pr["body"].replace(CONTROL, "e" * 40)
    elif mismatch == "source":
        github.record["source_base_sha"] = "e" * 40
    elif mismatch in {"attempt", "revision"}:
        github.record[mismatch] = 3
    elif mismatch == "run_id":
        request = FinalizerRequest(102, 11)
    elif mismatch == "workflow":
        github.run["path"] = ".github/workflows/other.yml"
    elif mismatch == "target":
        github.pr["baseRefName"] = "production"
    elif mismatch == "head":
        github.pr["headRefOid"] = "e" * 40
    elif mismatch == "merged":
        github.pr["mergedAt"] = "2026-01-01"
    elif mismatch == "queue":
        github.issue["comments"][0]["body"] = github.issue["comments"][0]["body"].replace(
            "DISPATCHED", "WAITING"
        )
    elif mismatch == "checkpoint":
        github.issue["comments"][1]["body"] = github.issue["comments"][1]["body"].replace(
            RUNNER_HEAD, "e" * 40
        )
    elif mismatch == "product_job_success":
        github.jobs[0]["conclusion"] = "success"
    else:
        github.issue["comments"][1]["user"]["login"] = "untrusted-user"
    with pytest.raises((SourceTerminalError, ValueError)):
        reconcile(github, mail, request)
    assert github.mutations == mail.sent == mail.trashed == []


def test_exact_owner_orphan_reconciliation_without_legacy_checkpoint():
    github, mail = GitHub(checkpoint=False), Mail()
    request = FinalizerRequest(101, 11, 7, HEAD, owner_reconciliation=True)
    assert reconcile(github, mail, request)["result"] == "CANCELLED"
    assert github.pr["state"] == github.issue["state"] == "CLOSED"


def test_legacy_started_without_exact_pr_checkpoint_is_not_adopted():
    github, mail = GitHub(checkpoint=False), Mail()
    with pytest.raises(SourceTerminalError, match="checkpoint"):
        reconcile(github, mail)
    assert github.mutations == mail.sent == []


def test_notification_residue_is_retained_then_reconciled_without_resending():
    github, mail = GitHub(), Mail()
    mail.fail_readback = True
    assert reconcile(github, mail)["notification_result"] == "RESIDUE"
    assert mail.messages[mail.started] == {"INBOX"}
    assert github.issue["state"] == "CLOSED"
    mail.fail_readback = False
    assert reconcile(github, mail)["notification_result"] == "DELIVERED"
    assert mail.messages[mail.started] == {"TRASH"}
    assert len(mail.sent) == github.mutations.count("add_comment") == 1


def test_pr_close_residue_preserves_result_and_history():
    github, mail = GitHub(), Mail()
    github.close_error = True
    outcome = reconcile(github, mail)
    assert outcome["result"] == "CANCELLED" and outcome["pr_cleanup"] == "RESIDUE"
    assert github.pr["state"] == "OPEN" and github.pr["headRefOid"] == HEAD
    assert github.issue["state"] == "CLOSED"
    github.close_error = False
    assert reconcile(github, mail)["pr_cleanup"] == "CLOSED"
    assert len(mail.sent) == 1
