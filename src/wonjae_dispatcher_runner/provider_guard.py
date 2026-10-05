"""Select exact trusted provider lifecycle evidence for terminal notification fallback."""

from __future__ import annotations

import re

HEADINGS = {
    "## Dispatcher v2 provider completion evidence": "terminal",
    "## Dispatcher v2 provider QUEUED evidence": "queued",
    "## Dispatcher v2 provider STARTED evidence": "started",
}
BOT_LOGINS = {"github-actions", "github-actions[bot]"}


def exact_provider_wake(issues, *, title, owner):
    matches = [
        issue for issue in issues
        if not issue.get("pull_request") and issue.get("title") == title
        and (issue.get("user") or issue.get("author") or {}).get("login") == owner
    ]
    if len(matches) != 1:
        raise ValueError("expected one exact Owner-authored provider wake")
    return matches[0]


def provider_lifecycle(comments, *, task_id, control_sha, source_sha):
    expected = {"task_id": task_id, "control_sha": control_sha, "source_sha": source_sha}
    latest = {}
    for comment in comments:
        if (comment.get("user") or comment.get("author") or {}).get("login") not in BOT_LOGINS:
            continue
        body = str(comment.get("body") or "")
        heading = body.split("\n", 1)[0]
        kind = HEADINGS.get(heading)
        if not kind:
            continue
        pairs = re.findall(r"^- ([a-z_]+): `([^`\r\n]+)`$", body, re.MULTILINE)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError("ambiguous provider lifecycle fields")
        if any(fields.get(key) != value for key, value in expected.items()):
            continue
        latest[kind] = fields
    terminal, started = latest.get("terminal", {}), latest.get("started", {})
    values = {
        "PROVIDER_TERMINAL_EXISTS": "true" if terminal else "false",
        # QUEUED is historical after terminal completion; it must not suppress
        # idempotent RESULT fallback when the primary notification failed.
        "PROVIDER_QUEUED_EXISTS": "true" if "queued" in latest and not terminal else "false",
        "PROVIDER_STARTED_EXISTS": "true" if started else "false",
    }
    if terminal:
        result = terminal.get("result")
        if result not in {"PASS", "FAILED", "MANUAL_REQUIRED", "CANCELLED", "CANCELED"}:
            raise ValueError("invalid exact provider terminal result")
        values["PROVIDER_TERMINAL_RESULT"] = result
    if started:
        for field, key in (("attempt", "ATTEMPT"), ("revision", "REVISION")):
            value = started.get(field, "")
            if not re.fullmatch(r"[1-9][0-9]*", value):
                raise ValueError("invalid exact provider attempt/revision")
            values[key] = value
        action = started.get("action")
        if action not in {
            "eas_update", "eas_workflow_update", "github_workflow_dispatch", "eas_build",
        }:
            raise ValueError("invalid exact provider action")
        values["PROVIDER_ACTION"] = action
    return values
