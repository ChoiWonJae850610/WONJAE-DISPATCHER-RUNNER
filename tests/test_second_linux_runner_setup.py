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
    monkeypatch.setattr(
        setup, "inspect_user_systemd", lambda: {
            "user_runtime_dir_owned": True,
            "user_linger": "yes",
            "user_systemd_manager": "running",
            "user_systemd_accessible": True,
            "user_service_candidate": True,
            "user_service_blocker": "NONE",
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


@pytest.mark.parametrize(
    ("nonroot", "runtime_owned", "linger", "manager", "expected"),
    [
        (True, True, "yes", "running", "NONE"),
        (True, True, "yes", "degraded", "NONE"),
        (True, True, "no", "running", "USER_LINGER_DISABLED"),
        (True, True, "unknown", "running", "USER_LINGER_UNVERIFIED"),
        (True, True, "yes", "unavailable", "USER_MANAGER_UNAVAILABLE"),
        (True, False, "yes", "running", "USER_RUNTIME_DIR_UNVERIFIED"),
        (False, True, "yes", "running", "USER_SERVICE_NONROOT_REQUIRED"),
    ],
)
def test_user_service_gate_is_separate_from_sudo_and_fails_closed(
    nonroot, runtime_owned, linger, manager, expected,
):
    assert setup.user_service_blocker(
        nonroot=nonroot, runtime_owned=runtime_owned,
        linger=linger, manager=manager,
    ) == expected


def test_user_service_read_only_systemd_commands_never_mutate(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        output = "yes\n" if args[0] == "loginctl" else "degraded\n"
        return SimpleNamespace(
            stdout=output, returncode=0 if args[0] == "loginctl" else 1,
        )

    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "XDG_RUNTIME_DIR": "/run/user/1000"}
    assert setup._readonly_systemd_query(
        ["loginctl", "show-user", "1000", "--property=Linger", "--value"],
        env=env,
    ) == "yes"
    assert setup._readonly_systemd_query(
        ["systemctl", "--user", "is-system-running"],
        env=env, allow_degraded=True,
    ) == "degraded"
    assert [x[0] for x in calls] == [
        ["loginctl", "show-user", "1000", "--property=Linger", "--value"],
        ["systemctl", "--user", "is-system-running"],
    ]
    for _, kwargs in calls:
        assert kwargs["timeout"] == 5
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert kwargs["env"] == env
        assert "shell" not in kwargs
    assert not any(
        command in args
        for args, _ in calls
        for command in ("enable-linger", "daemon-reload", "start", "enable")
    )


def test_systemd_probe_handles_unavailable_bus_and_timeout(monkeypatch):
    def failed(*args, **kwargs):
        raise setup.subprocess.TimeoutExpired(["systemctl"], timeout=5)

    monkeypatch.setattr(setup.subprocess, "run", failed)
    assert setup._readonly_systemd_query(
        ["systemctl", "--user", "is-system-running"],
        env={"PATH": "/usr/bin:/bin"},
        allow_degraded=True,
    ) is None


def test_rootless_probe_is_never_reused_as_automatic_activation_authority(monkeypatch):
    # The old install path must remain explicitly gated by sudo; a successful
    # user-service candidate is only a diagnostic until a separate trusted PR.
    facts = {
        "linux": True,
        "wsl2": True,
        "systemd": True,
        "sudo_noninteractive": False,
        "capacity_ok": True,
        "user_linger": "yes",
        "user_systemd_accessible": True,
        "user_service_candidate": True,
    }
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_HOST_BLOCKED_SUDO_NONINTERACTIVE"):
        setup.require_activation_host(facts)



def test_verify_missing_gh_cli_has_distinct_nonsecret_failure(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        setup, "git_variables", lambda _repo: pytest.fail("must not query variables"),
    )
    with pytest.raises(
        setup.SetupBlocked, match="^SECOND_RUNNER_VERIFY_GH_CLI_MISSING$",
    ):
        setup.verify_second_runner("example/repo", "runner-a", "runner-b")


def test_verify_missing_github_token_has_distinct_failure(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        setup, "git_variables", lambda _repo: pytest.fail("must not query variables"),
    )
    with pytest.raises(
        setup.SetupBlocked, match="^SECOND_RUNNER_GITHUB_AUTH_MISSING$",
    ):
        setup.verify_second_runner("example/repo", "runner-a", "runner-b")


def test_verify_variables_command_failure_never_exposes_gh_output(
    monkeypatch, capsys,
):
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setenv("GH_TOKEN", "test-private-token")

    def reject_variables_command(args, **kwargs):
        assert args[:3] == ["gh", "variable", "list"]
        return SimpleNamespace(
            returncode=1,
            stdout="test-private-token",
            stderr="403 test-private-token",
        )

    monkeypatch.setattr(setup.subprocess, "run", reject_variables_command)
    monkeypatch.setattr(
        setup, "gh_api", lambda _endpoint: pytest.fail("must stop before runners"),
    )
    with pytest.raises(
        setup.SetupBlocked, match="^SECOND_RUNNER_VERIFY_VARIABLES_QUERY_FAILED$",
    ) as exc:
        setup.verify_second_runner("example/repo", "runner-a", "runner-b")
    assert "test-private-token" not in str(exc.value)
    assert capsys.readouterr().out == ""


def test_verify_runners_api_failure_identified_after_matching_variables(
    monkeypatch, capsys,
):
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setenv("GH_TOKEN", "test-private-token")
    monkeypatch.setattr(
        setup, "git_variables", lambda _repo: {
            "DW_PRIMARY_RUNNER_NAME": "runner-a",
            "DW_SECONDARY_RUNNER_NAME": "runner-b",
        },
    )
    seen = []

    def reject_runner_api(endpoint):
        seen.append(endpoint)
        raise setup.SetupBlocked("SECOND_RUNNER_COMMAND_FAILED")

    monkeypatch.setattr(setup, "gh_api", reject_runner_api)
    with pytest.raises(
        setup.SetupBlocked, match="^SECOND_RUNNER_VERIFY_RUNNERS_API_FAILED$",
    ) as exc:
        setup.verify_second_runner("example/repo", "runner-a", "runner-b")
    assert seen == ["repos/example/repo/actions/runners?per_page=100"]
    assert "test-private-token" not in str(exc.value)
    assert capsys.readouterr().out == ""


def test_verify_existing_variable_and_inventory_guards_remain(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setenv("GH_TOKEN", "test-private-token")
    monkeypatch.setattr(
        setup, "git_variables", lambda _repo: {
            "DW_PRIMARY_RUNNER_NAME": "runner-a",
            "DW_SECONDARY_RUNNER_NAME": "runner-b",
        },
    )
    monkeypatch.setattr(
        setup, "gh_api", lambda _endpoint: {
            "total_count": 2,
            "runners": [fake_runner("runner-a"), fake_runner("runner-b")],
        },
    )
    assert setup.verify_second_runner("example/repo", "runner-a", "runner-b") is None
    with pytest.raises(setup.SetupBlocked, match="SECOND_RUNNER_VARIABLES_NOT_READY"):
        setup.verify_second_runner("example/repo", "runner-a", "wrong-b")


def test_verify_invalid_variables_response_keeps_existing_error(monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setenv("GH_TOKEN", "test-private-token")

    def invalid_variables(_repo):
        raise setup.SetupBlocked("SECOND_RUNNER_VARIABLES_INVALID")

    monkeypatch.setattr(setup, "git_variables", invalid_variables)
    with pytest.raises(setup.SetupBlocked, match="^SECOND_RUNNER_VARIABLES_INVALID$"):
        setup.verify_second_runner("example/repo", "runner-a", "runner-b")

def test_inspection_source_contains_no_linger_mutation_or_rootless_service_start():
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "second_linux_runner_setup.py"
    ).read_text(encoding="utf-8")
    assert "loginctl" in source
    assert '"show-user"' in source
    assert '"--property=Linger"' in source
    assert '"is-system-running"' in source
    assert "enable-linger" not in source
    assert "systemctl --user enable" not in source
    assert "systemctl --user start" not in source
