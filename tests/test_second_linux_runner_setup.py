"""Secret-free regression checks for the prepared remote second-runner route."""

from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

_spec = spec_from_file_location(
    "second_linux_runner_setup",
    Path(__file__).resolve().parents[1] / "scripts" / "second_linux_runner_setup.py",
)
assert _spec is not None and _spec.loader is not None
setup = module_from_spec(_spec)
_spec.loader.exec_module(setup)


def fake_runner(name: str, *, status: str = "online") -> dict:
    return {
        "name": name,
        "status": status,
        "labels": [{"name": s} for s in ("self-hosted", "Linux", "X64", "direct-worker")],
    }


def test_name_gate_requires_an_exact_primary_job_and_distinct_names():
    setup.check_names("primary", "secondary", "primary", "inspect")
    setup.check_names("primary", "secondary", "primary", "activate")
    setup.check_names("primary", "secondary", "secondary", "verify")
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_PRIMARY_HOST_MISMATCH"):
        setup.check_names("primary", "secondary", "secondary", "activate")
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_NAME_INVALID"):
        setup.check_names("primary", "primary", "primary", "activate")
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_NAME_INVALID"):
        setup.check_names("primary", "secondary;touch /tmp/x", "primary", "activate")


def test_runner_inventory_detects_offline_duplicate_and_missing_labels():
    valid = {"total_count": 1, "runners": [fake_runner("primary")]}
    setup.runner_inventory(valid, "primary", "secondary", require_secondary=False)
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_REQUIRED_HOST_NOT_ONLINE"):
        setup.runner_inventory(
            {"total_count": 1, "runners": [fake_runner("primary", status="offline")]},
            "primary", "secondary", require_secondary=False,
        )
    two = {"total_count": 2, "runners": [fake_runner("primary"), fake_runner("secondary")]}
    setup.runner_inventory(two, "primary", "secondary", require_secondary=True)
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_ALREADY_REGISTERED"):
        setup.runner_inventory(two, "primary", "secondary", require_secondary=False)
    two["runners"][1]["labels"] = []
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_LABELS_MISMATCH"):
        setup.runner_inventory(two, "primary", "secondary", require_secondary=True)


def test_release_requires_official_url_and_sha256_digest():
    release = {"assets": [{
        "name": "actions-runner-linux-x64-2.333.0.tar.gz",
        "browser_download_url": (
            "https://github.com/actions/runner/releases/download/v2.333.0/"
            "actions-runner-linux-x64-2.333.0.tar.gz"
        ),
        "digest": "sha256:" + "a" * 64,
    }]}
    url, digest = setup.select_runner_asset(release)
    assert url.startswith("https://github.com/actions/runner/releases/")
    assert digest == "a" * 64
    release["assets"][0]["browser_download_url"] = "https://example.invalid/malware"
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_RELEASE_URL_UNTRUSTED"):
        setup.select_runner_asset(release)


def test_host_activation_requires_systemd_sudo_and_available_resources():
    facts = {
        "linux": True, "wsl2": True, "systemd": True,
        "sudo_noninteractive": True, "capacity_ok": True,
    }
    setup.require_activation_host(facts)
    for value in ("systemd", "sudo_noninteractive", "capacity_ok"):
        invalid = dict(facts)
        invalid[value] = False
        with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_HOST_BLOCKED"):
            setup.require_activation_host(invalid)


def test_secondary_home_never_reuses_existing_path(tmp_path):
    root = setup.second_root(tmp_path)
    assert not root.exists()
    root.mkdir()
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_ROOT_ALREADY_EXISTS"):
        setup.second_root(tmp_path)


def test_child_commands_do_not_inherit_codex_or_owner_auth(monkeypatch):
    seen = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs.get("env", {}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("PRIMARY_CODEX_AUTH", "private-primary")
    monkeypatch.setenv("SECONDARY_CODEX_AUTH", "private-secondary")
    monkeypatch.setenv("GH_TOKEN", "private-owner")
    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    setup.safe_command(["tar", "--version"])
    setup.safe_command(["gh", "--version"])
    assert "PRIMARY_CODEX_AUTH" not in seen[0]
    assert "SECONDARY_CODEX_AUTH" not in seen[0]
    assert "GH_TOKEN" not in seen[0]
    assert seen[1].get("GH_TOKEN") == "private-owner"
    assert "PRIMARY_CODEX_AUTH" not in seen[1]


def test_inspect_path_is_read_only_even_without_credentials(monkeypatch, capsys):
    monkeypatch.setenv("DW_PRIMARY_NAME", "runner-a")
    monkeypatch.setenv("DW_SECONDARY_NAME", "runner-b")
    monkeypatch.setenv("RUNNER_NAME", "runner-a")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/runner")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        setup, "host_facts", lambda unused: {
            "linux": True, "wsl2": True, "systemd": False,
            "sudo_noninteractive": False, "memory_gib": 8,
            "disk_gib": 30, "capacity_ok": True,
        },
    )
    monkeypatch.setattr(sys, "argv", ["setup", "--operation", "inspect"])
    assert setup.main() == 0
    printed = capsys.readouterr().out
    assert "SECOND_RUNNER_INSPECT_ONLY_NO_MUTATION" in printed
    assert json.loads(printed.split("SECOND_RUNNER_INSPECTION=")[1].splitlines()[0])[
        "systemd"
    ] is False


def test_workflows_keep_owner_only_manual_activation_and_primary_secret_immutable():
    root = Path(__file__).resolve().parents[1]
    auth_path = root / ".github/workflows/bootstrap-secondary-codex-auth.yml"
    install_path = root / ".github/workflows/second-linux-runner-remote-setup.yml"
    auth = auth_path.read_text(encoding="utf-8")
    installer = install_path.read_text(encoding="utf-8")
    auth_doc = yaml.safe_load(auth)
    install_doc = yaml.safe_load(installer)

    assert "workflow_dispatch:" in auth
    assert "workflow_dispatch:" in installer
    assert "pull_request_target" not in auth + installer
    assert "on:\n  push:" not in auth + installer
    assert "gh secret set CODEX_AUTH_JSON_SECONDARY" in auth
    assert "gh secret set CODEX_AUTH_JSON --repo" not in auth
    assert "require_independent_auth" in auth
    assert "SECONDARY_CODEX_AUTH_ALREADY_EXISTS" in auth
    assert "github.actor == github.repository_owner" in auth
    assert "github.actor == github.repository_owner" in installer
    assert auth_doc["jobs"]["bootstrap-secondary"]["runs-on"] == "ubuntu-latest"
    job = install_doc["jobs"]["remote-setup"]
    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "direct-worker"]
    assert "DW_PRIMARY_NAME" in job["env"]
    assert "DW_SECONDARY_NAME" in job["env"]
    steps = job["steps"]
    assert len(steps) == 4
    assert "PRIMARY_CODEX_AUTH" not in str(steps[1])
    assert "SECONDARY_CODEX_AUTH" not in str(steps[1])
    assert steps[3]["env"]["SECONDARY_CODEX_AUTH"] == (
        "$"+"{{ secrets.CODEX_AUTH_JSON_SECONDARY }}"
    )
    assert all(step["name"] != "Start product Direct Worker" for step in steps)
