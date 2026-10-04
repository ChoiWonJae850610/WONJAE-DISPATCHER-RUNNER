import importlib.util
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/github_provider_terminal.py"
SPEC = importlib.util.spec_from_file_location("github_terminal_test", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

IDENTITY = {
    "task_id": "ESC-TEST-001",
    "attempt": "2",
    "revision": "2",
    "control_sha": "a" * 40,
    "source_sha": "b" * 40,
    "action": "github_workflow_dispatch",
}


def run_payload(conclusion="success", status="completed"):
    return {
        "id": 900,
        "repository": {"full_name": "owner/product"},
        "head_repository": {"full_name": "owner/product"},
        "workflow_id": 42,
        "path": ".github/workflows/deploy.yml",
        "head_sha": "b" * 40,
        "event": "workflow_dispatch",
        "status": status,
        "conclusion": conclusion,
    }


def validate(value):
    return MODULE.validate_run(
        value,
        repository="owner/product",
        workflow_id=42,
        workflow_file="deploy.yml",
        run_id=900,
        source_sha="b" * 40,
    )


@pytest.mark.parametrize(
    "conclusion,result",
    [
        ("success", "PASS"),
        ("failure", "FAILED"),
        ("cancelled", "FAILED"),
        ("timed_out", "FAILED"),
        ("action_required", "MANUAL_REQUIRED"),
        ("skipped", "MANUAL_REQUIRED"),
        ("neutral", "MANUAL_REQUIRED"),
    ],
)
def test_exact_github_conclusions(conclusion, result):
    assert validate(run_payload(conclusion)) == result


@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "c" * 40),
        ("id", 901),
        ("workflow_id", 43),
        ("path", ".github/workflows/other.yml"),
        ("repository", {"full_name": "owner/other"}),
        ("head_repository", {"full_name": "fork/product"}),
    ],
)
def test_identity_mismatch_fails_closed(field, value):
    run = run_payload()
    run[field] = value
    with pytest.raises(MODULE.IdentityError):
        validate(run)


def test_wait_is_bounded_and_only_reads_exact_run():
    clock = [0]
    reads = []

    def read():
        reads.append(900)
        return "QUEUED"

    def sleep(seconds):
        clock[0] += seconds

    assert MODULE.bounded_result(read, seconds=31, clock=lambda: clock[0], sleep=sleep) == "QUEUED"
    assert clock[0] == 31
    assert reads == [900] * 4
    with pytest.raises(ValueError):
        MODULE.bounded_result(read, seconds=601)


def comment(heading, values):
    return {
        "user": {"login": "github-actions[bot]"},
        "body": "\n".join([heading, "", *(f"- {k}: `{v}`" for k, v in values.items())]),
    }


def test_launch_cannot_dispatch_twice_or_adopt_untrusted_handoff():
    queued = comment(MODULE.QUEUED, {**IDENTITY, "provider_run_id": "900"})
    assert MODULE.launch_gate([queued], IDENTITY) == "reconcile"
    started = comment("## Dispatcher v2 provider STARTED evidence", IDENTITY)
    with pytest.raises(MODULE.IdentityError):
        MODULE.launch_gate([started], IDENTITY)
    queued["user"]["login"] = "someone-else"
    assert MODULE.launch_gate([queued], IDENTITY) == "ready"


class Mailbox:
    def __init__(self):
        self.inbox = {"ESC|ESC-TEST-001|2|STARTED", "WAFL|WAFL-OTHER-001|1|STARTED"}
        self.trash = set()
        self.sent = []

    def notification_exists(self, key):
        return key in self.inbox or key in self.trash

    def send(self, note):
        self.sent.append(note.key)
        self.inbox.add(note.key)

    def wait_for_notification(self, key, **kwargs):
        return key in self.inbox

    def apply_label(self, key, label):
        pass

    def trash_notification(self, key):
        if key not in self.inbox and key not in self.trash:
            return False
        self.inbox.discard(key)
        self.trash.add(key)
        return key not in self.inbox


@pytest.mark.parametrize(
    "conclusion,result,status",
    [
        ("success", "PASS", "COMPLETED"),
        ("failure", "FAILED", "FAILED"),
        ("cancelled", "FAILED", "FAILED"),
        ("action_required", "MANUAL_REQUIRED", "MANUAL_REQUIRED"),
    ],
)
def test_fast_terminal_then_late_status_is_idempotent_and_cleans_started(
    monkeypatch, conclusion, result, status
):
    comments = [comment(MODULE.QUEUED, {**IDENTITY, "provider_run_id": "900"})]
    state = {"issue": "open", "writes": 0, "closes": 0, "reads": 0}

    def api(path, *, token, method="GET", payload=None):
        if "/comments?" in path:
            return comments
        if path.endswith("/comments"):
            state["writes"] += 1
            comments.append({"user": {"login": "github-actions[bot]"}, "body": payload["body"]})
            return {}
        if path.endswith("/issues/10"):
            if method == "PATCH":
                state["closes"] += 1
                state["issue"] = "closed"
            return {"state": state["issue"]}
        assert token == "provider-read"
        if "/workflows/" in path:
            return {"id": 42, "path": ".github/workflows/deploy.yml"}
        assert path.endswith("/runs/900")
        state["reads"] += 1
        return run_payload(conclusion)

    for key, value in IDENTITY.items():
        monkeypatch.setenv(key.upper() if key != "action" else "PROVIDER_ACTION", value)
    for key, value in {
        "GITHUB_REPOSITORY": "owner/runner",
        "ISSUE_NUMBER": "10",
        "RUNNER_GH_TOKEN": "runner-write",
        "GH_TOKEN": "provider-read",
        "PRODUCT_REPOSITORY": "owner/product",
        "PROVIDER_WORKFLOW_FILE": "deploy.yml",
        "PROJECT": "ESC",
        "GMAIL_ENABLED": "true",
        "GMAIL_USERNAME": "test",
        "GMAIL_APP_PASSWORD": "test",
    }.items():
        monkeypatch.setenv(key, value)
    mailbox = Mailbox()
    monkeypatch.setattr(MODULE, "api", api)
    monkeypatch.setattr(MODULE, "GmailClient", lambda *_: mailbox)
    monkeypatch.setattr(sys, "argv", ["terminal", "--wait-seconds", "600"])
    assert MODULE.main() == 0
    assert MODULE.fields(comments[-1]["body"])["result"] == result
    monkeypatch.setattr(sys, "argv", ["terminal", "--wait-seconds", "0"])
    assert MODULE.main() == 0
    assert state == {"issue": "closed", "writes": 1, "closes": 1, "reads": 1}
    assert mailbox.sent == [f"ESC|ESC-TEST-001|2|{status}"]
    assert f"ESC|ESC-TEST-001|2|{status}" in mailbox.inbox
    assert "ESC|ESC-TEST-001|2|STARTED" not in mailbox.inbox
    assert "ESC|ESC-TEST-001|2|STARTED" in mailbox.trash
    assert "WAFL|WAFL-OTHER-001|1|STARTED" in mailbox.inbox


def test_timeout_then_manual_status_fallback():
    clock = [0]

    def sleep(seconds):
        clock[0] += seconds

    assert (
        MODULE.bounded_result(
            lambda: validate(run_payload(None, "in_progress")),
            seconds=30,
            clock=lambda: clock[0],
            sleep=sleep,
        )
        == "QUEUED"
    )
    assert MODULE.bounded_result(lambda: validate(run_payload()), seconds=0) == "PASS"


def test_eas_launch_and_status_remain_async_without_bounded_wait():
    launch = Path(".github/workflows/provider-action-core.yml").read_text()
    step = launch.split("GitHub provider · bounded exact-run terminal reconciliation", 1)[1]
    step = step.split("      - name:", 1)[0]
    assert "env.PROVIDER_ACTION == 'github_workflow_dispatch'" in step
    assert "--wait-seconds 600" in step
    assert "eas workflow:run" in launch
    assert "eas build" in launch
    assert "--no-wait" in launch
