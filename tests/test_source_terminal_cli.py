import importlib.util
import json
from argparse import Namespace
from copy import deepcopy
from pathlib import Path

import pytest
from test_source_terminal import CONTROL, HEAD, OWNER, REPOSITORY, GitHub, Mail, reconcile

from wonjae_dispatcher_runner.source_terminal import CHECKPOINT_HEADING, SourceTerminalError

SPEC = importlib.util.spec_from_file_location(
    "source_terminal_cli_tests", Path(__file__).resolve().parents[1] / "scripts/source_terminal.py"
)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def setup_environment(tmp_path, monkeypatch, github):
    work, event, output = (tmp_path / name for name in ("work.json", "event.json", "output"))
    work.write_text(json.dumps(github.record))
    for key, value in {
        "WORK_ORDER_FILE": str(work),
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REPOSITORY_OWNER": OWNER,
        "GITHUB_RUN_ID": "101",
        "ISSUE_NUMBER": "11",
        "PILOT_PR_NUMBER": "7",
        "PILOT_HEAD_SHA": HEAD,
        "CONTROL_SHA": CONTROL,
        "PRODUCT_GH_TOKEN": "synthetic-product-token",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "Client", lambda *args: github)
    monkeypatch.setattr(cli.queue, "gh_json", lambda *args: deepcopy(github.run))
    return event, output


def test_checkpoint_is_updated_after_repair_head_without_duplicate_comment(tmp_path, monkeypatch):
    github = GitHub(checkpoint=False)
    setup_environment(tmp_path, monkeypatch, github)
    cli.checkpoint()
    first = [c for c in github.issue["comments"] if CHECKPOINT_HEADING in c["body"]]
    assert len(first) == 1 and HEAD in first[0]["body"]
    new_head = "e" * 40
    github.pr["headRefOid"] = new_head
    monkeypatch.setenv("PILOT_HEAD_SHA", new_head)
    cli.checkpoint()
    second = [c for c in github.issue["comments"] if CHECKPOINT_HEADING in c["body"]]
    assert len(second) == 1 and new_head in second[0]["body"]
    assert github.mutations == ["add_comment", "update_comment", "update_comment"]


def test_wrong_pr_cannot_be_checkpointed(tmp_path, monkeypatch):
    github = GitHub(checkpoint=False)
    setup_environment(tmp_path, monkeypatch, github)
    github.pr["headRefName"] = "job/unrelated"
    with pytest.raises(SourceTerminalError, match="checkpoint PR"):
        cli.checkpoint()
    assert github.mutations == []


def test_owner_command_selects_only_existing_exact_run(tmp_path, monkeypatch):
    github = GitHub()
    event, output = setup_environment(tmp_path, monkeypatch, github)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    event.write_text(
        json.dumps(
            {
                "issue": {"number": 11, "user": {"login": OWNER}},
                "comment": {
                    "user": {"login": OWNER},
                    "body": f"[SOURCE-TERMINAL-RECONCILE] run=101 pr=7 head={HEAD}",
                },
            }
        )
    )
    assert cli.select() == 0
    assert "source_run_id=101" in output.read_text()
    assert "owner_reconciliation=true" in output.read_text()
    assert github.mutations == []


def test_non_owner_command_is_rejected_before_read_or_mutation(tmp_path, monkeypatch):
    github = GitHub()
    event, _ = setup_environment(tmp_path, monkeypatch, github)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    event.write_text(
        json.dumps(
            {
                "issue": {"number": 11, "user": {"login": OWNER}},
                "comment": {
                    "user": {"login": "other"},
                    "body": f"[SOURCE-TERMINAL-RECONCILE] run=101 pr=7 head={HEAD}",
                },
            }
        )
    )
    with pytest.raises(SourceTerminalError, match="Owner-authored"):
        cli.select()
    assert github.mutations == []


def test_cli_replays_already_closed_wake_without_duplicate_mutations(tmp_path, monkeypatch):
    github, mail = GitHub(), Mail()
    reconcile(github, mail)
    setup_environment(tmp_path, monkeypatch, github)
    for key in ("GMAIL_ENABLED", "GMAIL_USERNAME", "GMAIL_APP_PASSWORD"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setattr(cli, "GmailClient", lambda *args: mail)
    monkeypatch.setattr(cli.queue, "read_control_for_registration", lambda *args: github.record)

    def resolve(args):
        assert args.allow_closed is True
        Path(args.output).write_text(json.dumps(github.record))

    monkeypatch.setattr(cli.queue, "resolve_control", resolve)
    before = list(github.mutations)
    args = Namespace(
        project="CLASSMO",
        source_run_id=101,
        wake_issue_number=11,
        product_pr=None,
        last_head="",
        owner_reconciliation=False,
    )
    assert cli.finalize(args) == 0
    assert github.mutations == before
    assert len(mail.sent) == 1
