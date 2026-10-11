"""No-network synthetic SDK diagnostics and historical fixed-marker classification."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from wonjae_dispatcher_runner.codex_safe_diagnostic import (
    PROBE_PROMPT,
    classify_historic_log,
    format_probe_result,
    probe_injected_sdk,
)


def event(method: str, *, message_type=None, delta=None, status=None):
    item = SimpleNamespace(root=SimpleNamespace(type=message_type))
    return SimpleNamespace(
        method=method,
        payload=SimpleNamespace(
            item=item, delta=delta,
            turn=SimpleNamespace(status=SimpleNamespace(value=status)),
        ),
    )


def fake_probe(tmp_path, kind):
    workspace = tmp_path / "empty"
    workspace.mkdir()
    calls = []

    class FakeTurn:
        def stream(self):
            calls.append("stream")
            if kind == "no_event":
                return iter(())
            if kind == "turn_start_timeout":
                raise RuntimeError("synthetic transport failure with secret-like-data")
            if kind == "agent_wait_timeout":
                raise RuntimeError("synthetic transport failure with secret-like-data")
            if kind == "after_delta_timeout":
                def interrupted():
                    yield event("turn/started")
                    yield event("item/agentMessage/delta", delta="PRIVATE MESSAGE CONTENT")
                    raise RuntimeError("secret-like-data")
                return interrupted()
            if kind == "remote_failure":
                return iter([event("turn/started"), event("turn/completed", status="failed")])
            if kind == "failed_event":
                return iter([event("turn/started"), event("turn/failed")])
            if kind == "no_agent_output":
                return iter([event("turn/started"), event("turn/completed", status="completed")])
            return iter([
                event("turn/started"),
                event("item/agentMessage/delta", delta="PRIVATE MESSAGE CONTENT"),
                event("turn/completed", status="completed"),
            ])

    class FakeThread:
        def turn(self, prompt, **kwargs):
            calls.append("turn")
            assert prompt == PROBE_PROMPT
            assert kwargs["approval_mode"].value == "never" or kwargs["approval_mode"] is not None
            assert kwargs["sandbox"] is not None
            if kind == "turn_timeout":
                raise RuntimeError("secret-like-data")
            return FakeTurn()

    class FakeCodex:
        def __enter__(self):
            calls.append("enter")
            if kind == "sdk_timeout":
                raise RuntimeError("secret-like-data")
            return self

        def __exit__(self, *_args):
            calls.append("close")
            if kind == "close_timeout":
                raise RuntimeError("secret-like-data")

        def account(self, **kwargs):
            calls.append("account")
            assert kwargs == {"refresh_token": False}
            if kind == "account_timeout":
                raise RuntimeError("secret-like-data")
            return SimpleNamespace(account=None if kind == "missing_auth" else object())

        def thread_start(self, **kwargs):
            calls.append("thread")
            assert kwargs["cwd"] == str(workspace)
            assert kwargs["ephemeral"] is True
            assert kwargs["approval_mode"] is not None
            assert kwargs["sandbox"] is not None
            assert kwargs["config"]["history"] == {"persistence": "none"}
            if kind == "thread_timeout":
                raise RuntimeError("secret-like-data")
            return FakeThread()

    report = probe_injected_sdk(
        sdk_factory=FakeCodex,
        empty_workspace=workspace,
        clock=lambda: 10.0,
        deadline=lambda seconds: nullcontext(),
    )
    return report, calls


@pytest.mark.parametrize(
    ("kind", "expected_status", "expected_phase"),
    [
        ("success", "PASS", "sdk_close"),
        ("no_agent_output", "PASS_NO_AGENT_OUTPUT", "sdk_close"),
        ("missing_auth", "AUTH_SESSION_MISSING", "account_lookup"),
        ("sdk_timeout", "SDK_UNKNOWN_ERROR", "sdk_start"),
        ("account_timeout", "SDK_UNKNOWN_ERROR", "account_lookup"),
        ("thread_timeout", "SDK_UNKNOWN_ERROR", "thread_start"),
        ("turn_timeout", "SDK_UNKNOWN_ERROR", "turn_submit"),
        ("no_event", "TURN_NO_TERMINAL", "first_notification_wait"),
        ("agent_wait_timeout", "SDK_UNKNOWN_ERROR", "first_notification_wait"),
        ("after_delta_timeout", "SDK_UNKNOWN_ERROR", "agent_output_seen"),
        ("remote_failure", "TURN_NON_SUCCESS", "turn_completed"),
        ("failed_event", "TURN_NON_SUCCESS", "agent_output_wait"),
        ("close_timeout", "SDK_UNKNOWN_ERROR", "sdk_close"),
    ],
)
def test_stage_distinctions_and_bounded_outputs(tmp_path, kind, expected_status, expected_phase):
    result, calls = fake_probe(tmp_path, kind)
    assert result.status == expected_status
    assert result.last_phase == expected_phase
    assert "PRIVATE MESSAGE CONTENT" not in format_probe_result(result)
    assert "secret-like-data" not in format_probe_result(result)
    assert result.elapsed_ms == 0
    assert calls.count("enter") == 1
    assert calls.count("account") <= 1
    assert calls.count("turn") <= 1
    assert "credential_rotate" not in calls


def test_agent_delta_marks_progress_separately_from_turn_started(tmp_path):
    result, _ = fake_probe(tmp_path, "success")
    assert result.account_present is True
    assert result.turn_started is True
    assert result.first_agent_output is True
    assert result.completed is True


def test_no_content_event_not_equivalent_to_agent_output(tmp_path):
    result, _ = fake_probe(tmp_path, "no_agent_output")
    assert result.turn_started
    assert not result.first_agent_output
    assert result.completed


@pytest.mark.parametrize("seconds", [0, -1, 121, 481])
def test_probe_deadline_fail_closed_without_sdk_invocation(tmp_path, seconds):
    workspace = tmp_path / "empty"
    workspace.mkdir()
    with pytest.raises(ValueError, match="deadline"):
        probe_injected_sdk(
            sdk_factory=lambda: pytest.fail("factory should not run"),
            empty_workspace=workspace,
            deadline_seconds=seconds,
        )


def test_probe_rejects_nonempty_or_symlink_workspaces_without_sdk(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("sensitive", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        probe_injected_sdk(sdk_factory=lambda: pytest.fail("no SDK"), empty_workspace=workspace)
    workspace_link = tmp_path / "workspace-link"
    workspace_link.symlink_to(workspace, target_is_directory=True)
    with pytest.raises(ValueError, match="independent"):
        probe_injected_sdk(sdk_factory=lambda: pytest.fail("no SDK"), empty_workspace=workspace_link)


def test_historic_3x480_seconds_does_not_infer_missing_model_first_response():
    sha = "a" * 40
    raw = "\n".join(
        f"2026-10-11T01:00:00Z INITIAL_TURN attempt={i} "
        f"outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={sha}"
        for i in (1, 2, 3)
    ) + "\nINITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0"
    result = classify_historic_log(raw)
    assert result.status == "LEGACY_TIMEOUT_PHASE_UNKNOWN"
    assert result.attempts == 3
    assert result.phase == "unknown"


def test_new_phase_timing_marker_is_classified_without_disclosing_source_sha():
    sha = "a" * 40
    raw = "\n".join(
        f"INITIAL_TURN attempt={i} outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={sha}"
        for i in (1, 2, 3)
    ) + (
        "\nDIRECT_CODEX_TIMEOUT_PHASE phase=agent_output_seen elapsed_s=480"
        "\nINITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0"
    )
    result = classify_historic_log(raw)
    assert result.status == "TIMEOUT_PHASE_OBSERVED"
    assert result.phase == "agent_output_seen"
    assert sha not in repr(result)


def test_historic_identity_mismatch_rejected():
    raw = "\n".join(
        f"INITIAL_TURN attempt={i} outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={sha}"
        for i, sha in [(1, "a" * 40), (2, "b" * 40), (3, "a" * 40)]
    ) + "\nINITIAL_CODEX_TIMEOUT_EXHAUSTED attempts=3 commits=0"
    assert classify_historic_log(raw).status == "INSUFFICIENT_EVIDENCE"


def test_historic_duplicate_conflicting_sha_rejected():
    raw = (
        f"INITIAL_TURN attempt=1 outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={'a' * 40}\n"
        f"INITIAL_TURN attempt=1 outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source={'b' * 40}\n"
    )
    assert classify_historic_log(raw).status == "INCONSISTENT_IDENTITY"


def test_historic_log_size_is_bounded():
    with pytest.raises(ValueError, match="size"):
        classify_historic_log("X" * 500_001)


def test_existing_credential_rotating_smoke_is_not_used_by_diagnostic():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    existing = (root / ".github/workflows/direct-worker-home-codex-smoke.yml").read_text()
    assert "gh secret set CODEX_AUTH_JSON" in existing
    assert "Rotate credential after successful synthetic turn" in existing
    core = (root / "src/wonjae_dispatcher_runner/codex_safe_diagnostic.py").read_text()
    assert "gh secret set" not in core
    assert "CODEX_AUTH_JSON" not in core
    assert "workflow_dispatch" not in core
