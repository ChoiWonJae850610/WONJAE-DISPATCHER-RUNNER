import os
import subprocess
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.provider_guard import exact_provider_wake, provider_lifecycle

IDENTITY = {"task_id": "ESC-SYNTHETIC-001", "control_sha": "a" * 40, "source_sha": "b" * 40}


def comment(kind, *, author="github-actions[bot]", **overrides):
    fields = {**IDENTITY, "attempt": "1", "revision": "1", "action": "eas_update", **overrides}
    heading = {"queued": "QUEUED", "started": "STARTED", "terminal": "completion"}[kind]
    return {"user": {"login": author}, "body":
            f"## Dispatcher v2 provider {heading} evidence\n\n"
            + "\n".join(f"- {key}: `{value}`" for key, value in fields.items())}


def test_queued_history_does_not_suppress_terminal_result_notification_fallback():
    values = provider_lifecycle([
        comment("started"), comment("queued"), comment("terminal", result="PASS"),
    ], **IDENTITY)
    assert values["PROVIDER_TERMINAL_RESULT"] == "PASS"
    assert values["PROVIDER_QUEUED_EXISTS"] == "false"
    assert values["PROVIDER_TERMINAL_EXISTS"] == "true"
    assert values["ATTEMPT"] == "1"


def test_real_external_queue_stays_non_terminal_and_is_not_resubmitted():
    values = provider_lifecycle([comment("started"), comment("queued")], **IDENTITY)
    assert values["PROVIDER_QUEUED_EXISTS"] == "true"
    assert values["PROVIDER_TERMINAL_EXISTS"] == "false"


@pytest.mark.parametrize("override", [
    {"author": "outsider"}, {"author": "owner"}, {"task_id": "ESC-OTHER-001"},
    {"control_sha": "c" * 40}, {"source_sha": "d" * 40},
])
def test_untrusted_or_other_task_evidence_cannot_close_wake_or_fabricate_mail(override):
    values = provider_lifecycle([comment("terminal", result="PASS", **override)], **IDENTITY)
    assert values["PROVIDER_TERMINAL_EXISTS"] == "false"
    assert "PROVIDER_TERMINAL_RESULT" not in values


def test_duplicate_identity_fields_are_rejected():
    item = comment("terminal", result="PASS")
    item["body"] += f"\n- task_id: `{IDENTITY['task_id']}`"
    with pytest.raises(ValueError, match="ambiguous"):
        provider_lifecycle([item], **IDENTITY)


def test_exact_wake_selection_requires_unique_owner_issue_not_pr_or_spoof():
    wake = {"number": 7, "title": "exact title", "user": {"login": "owner"}}
    spoof = {**wake, "number": 8, "user": {"login": "outsider"}}
    pr = {**wake, "number": 9, "pull_request": {"url": "synthetic"}}
    assert exact_provider_wake([spoof, pr, wake], title="exact title", owner="owner") == wake
    for issues in ([spoof, pr], [wake, {**wake, "number": 10}]):
        with pytest.raises(ValueError, match="one exact"):
            exact_provider_wake(issues, title="exact title", owner="owner")


def test_guard_reads_all_pages_and_uses_shared_exact_evidence_selector():
    text = Path(".github/workflows/provider-terminal-guard.yml").read_text()
    assert text.count("gh api --paginate --slurp") == 2
    assert "exact_provider_wake(" in text and "provider_lifecycle(" in text
    assert "PROVIDER_QUEUED_EXISTS != 'true'" in text


@pytest.mark.parametrize("name,result", [("failure", "FAILED"), ("manual", "MANUAL_REQUIRED")])
def test_actual_provider_status_terminal_blocks_emit_complete_identity(tmp_path, name, result):
    text = Path(".github/workflows/provider-status-core.yml").read_text()
    end = text.index(f'          }} > "$RUNNER_TEMP/provider-{name}.md"')
    start = text.rfind("          {\n", 0, end)
    body = "\n".join(line[10:] for line in text[start:end].splitlines()) + "\n}"
    env = {**os.environ, "TASK_ID": IDENTITY["task_id"], "CONTROL_SHA": IDENTITY["control_sha"],
           "SOURCE_SHA": IDENTITY["source_sha"], "ATTEMPT": "1", "REVISION": "1",
           "PROVIDER_ACTION": "eas_update", "PROVIDER_RUN_ID": "synthetic-run",
           "PROVIDER_STATUS": "FAILURE"}
    output = subprocess.run(["bash", "-euo", "pipefail", "-c", body], env=env,
                            capture_output=True, text=True, check=True).stdout
    terminal = {"user": {"login": "github-actions[bot]"}, "body": output}
    values = provider_lifecycle([comment("started"), comment("queued"), terminal], **IDENTITY)
    assert values["PROVIDER_TERMINAL_RESULT"] == result
    assert values["PROVIDER_QUEUED_EXISTS"] == "false"
