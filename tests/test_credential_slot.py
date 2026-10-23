"""Offline two-slot admission and workflow contract regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from wonjae_dispatcher_runner.credential_slot import (
    CredentialSlotError,
    main,
    require_independent_auth,
    resolve_credential_slot,
)


def auth(refresh: str) -> str:
    return json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "synthetic-access", "refresh_token": refresh},
    })


def test_legacy_single_runner_needs_no_new_variables():
    assert resolve_credential_slot("current-linux-runner", "", "") == "legacy"


def test_both_registered_names_select_exactly_one_slot():
    assert resolve_credential_slot("runner-a", "runner-a", "runner-b") == "primary"
    assert resolve_credential_slot("runner-b", "runner-a", "runner-b") == "secondary"


@pytest.mark.parametrize(
    ("actual", "primary", "secondary", "failure_code"),
    [
        ("runner-a", "runner-a", "", "CODEX_SLOT_RUNNER_NAMES_INCOMPLETE"),
        ("runner-a", "", "runner-b", "CODEX_SLOT_RUNNER_NAMES_INCOMPLETE"),
        ("runner-a", "runner-a", "runner-a", "CODEX_SLOT_RUNNER_NAMES_DUPLICATE"),
        ("unrecognized", "runner-a", "runner-b", "CODEX_SLOT_RUNNER_NOT_REGISTERED"),
    ],
)
def test_bad_registration_fails_closed(actual, primary, secondary, failure_code):
    with pytest.raises(CredentialSlotError, match=failure_code):
        resolve_credential_slot(actual, primary, secondary)


def test_independent_refresh_sessions_accepted():
    require_independent_auth(auth("refresh-slot-a"), auth("refresh-slot-b"))


def test_reused_refresh_session_rejected_without_credential_output():
    with pytest.raises(CredentialSlotError) as caught:
        require_independent_auth(auth("same-private-refresh"), auth("same-private-refresh"))
    assert str(caught.value) == "CODEX_SLOT_AUTH_SHARED_SESSION"
    assert "same-private-refresh" not in str(caught.value)


@pytest.mark.parametrize("invalid", ["", "not-json", "{}", '{"tokens": {}}'])
def test_missing_or_invalid_secondary_auth_fails_closed(invalid):
    with pytest.raises(CredentialSlotError, match="CODEX_SLOT_AUTH_INVALID"):
        require_independent_auth(auth("refresh-slot-a"), invalid)


def test_cli_writes_only_trusted_enum_without_runner_names(tmp_path, monkeypatch, capsys):
    output = tmp_path / "GITHUB_OUTPUT"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("DW_ACTUAL_RUNNER_NAME", "runner-b")
    monkeypatch.setenv("DW_PRIMARY_RUNNER_NAME", "runner-a")
    monkeypatch.setenv("DW_SECONDARY_RUNNER_NAME", "runner-b")
    monkeypatch.setattr(sys, "argv", ["credential_slot", "--github-output", str(output)])
    assert main() == 0
    assert output.read_text(encoding="utf-8") == "slot=secondary\n"
    assert capsys.readouterr().out.strip() == "CODEX_CREDENTIAL_SLOT=secondary"


def test_cli_auth_guard_requires_exact_parallel_slot(monkeypatch):
    monkeypatch.setenv("DW_CREDENTIAL_SLOT", "legacy")
    monkeypatch.setenv("CODEX_AUTH_JSON_PRIMARY", auth("refresh-a"))
    monkeypatch.setenv("CODEX_AUTH_JSON_SECONDARY", auth("refresh-b"))
    monkeypatch.setattr(sys, "argv", ["credential_slot", "--check-auth"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2


def test_workflow_keeps_project_exclusion_and_routes_secondary_secret():
    root = Path(__file__).resolve().parents[1]
    caller = yaml.safe_load(
        (root / ".github/workflows/direct-worker.yml").read_text(encoding="utf-8")
    )
    core_path = root / ".github/workflows/direct-worker-core.yml"
    core_text = core_path.read_text(encoding="utf-8")
    core = yaml.safe_load(core_text)

    assert core["concurrency"] == {
        "group": "direct-worker-${{ inputs.project }}",
        "cancel-in-progress": False,
    }
    assert "self-hosted" in core["jobs"]["direct-worker"]["runs-on"]
    assert "direct-worker" in core["jobs"]["direct-worker"]["runs-on"]
    assert set(caller["jobs"]) == {"wafl", "classmo", "esc", "muvel"}
    for job in caller["jobs"].values():
        assert job["secrets"]["codex_auth_json"] == "${{ secrets.CODEX_AUTH_JSON }}"
        assert job["secrets"]["codex_auth_json_secondary"] == (
            "${{ secrets.CODEX_AUTH_JSON_SECONDARY }}"
        )
    steps = core["jobs"]["direct-worker"]["steps"]
    names = [s["name"] for s in steps]
    assert "Resolve isolated Codex credential slot" in names
    assert "Run isolated Codex Direct Worker" in names
    assert "Rotate refreshed Codex session" in names
    assert "codex_auth_json_secondary" in core_text
    assert "CODEX_AUTH_JSON_SECONDARY" in core_text
    assert 'secret_name="CODEX_AUTH_JSON_SECONDARY"' in core_text
    assert "DW_CREDENTIAL_SLOT" in core_text
