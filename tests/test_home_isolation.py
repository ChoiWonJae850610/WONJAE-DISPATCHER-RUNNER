from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from wonjae_dispatcher_runner.home_isolation import (
    HOME_LABELS,
    PROFILE,
    cli_overrides,
    permission_config,
    protected_mount_placeholders,
    source_environment,
)


@pytest.mark.skipif(os.name != "posix", reason="actual source runtime requires Linux")
def test_actual_source_sdk_receives_no_secrets_and_retains_split_profile(tmp_path, monkeypatch):
    from test_direct_worker import git, registry

    from wonjae_dispatcher_runner import direct_worker as worker

    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "synthetic@example.invalid")
    git(repo, "config", "user.name", "Synthetic")
    (repo / "source.txt").write_text("before\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    route = worker.load_direct_worker_route(registry(tmp_path), "ESC")
    environment = dict(os.environ)
    environment.update({
        "DIRECT_WORKER_HOME_ISOLATION": "1", "PRODUCT_TOKEN": "synthetic-secret",
        "RUNNER_TOKEN": "synthetic-secret", "GH_TOKEN": "synthetic-secret",
        "CODEX_AUTH_JSON": "synthetic-secret", "PROVIDER_TOKEN": "synthetic-secret",
    })
    monkeypatch.setattr(worker.os, "environ", environment)

    class FakeCodex:
        def __init__(self, config):
            assert "synthetic-secret" not in json.dumps(dict(worker.os.environ))
            assert config.env == source_environment(tmp_path / "auth", Path("/nonexistent"))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def account(self, **kwargs):
            return SimpleNamespace(account=object())

        def thread_start(self, **kwargs):
            assert kwargs["approval_mode"] == worker.ApprovalMode.deny_all
            assert kwargs["sandbox"] is None  # Legacy override must not drop read restrictions.
            assert kwargs["config"]["default_permissions"] == PROFILE
            assert kwargs["config"]["permissions"] == permission_config(repo)["permissions"]
            assert kwargs["config"]["shell_environment_policy"]["inherit"] == "none"
            return self

        def run(self, *args, **kwargs):
            assert kwargs["sandbox"] is None
            assert kwargs["approval_mode"] == worker.ApprovalMode.deny_all
            (repo / "source.txt").write_text("after\n")
            return SimpleNamespace(status="completed", error=None, final_response=json.dumps({
                "status": "CHANGED", "summary": "Synthetic", "manual_action": "",
            }))

    monkeypatch.setattr(worker, "Codex", FakeCodex)
    assert worker.run_direct_worker(repo, route, "next", tmp_path / "auth").status == "CHANGED"


def checkout(tmp_path):
    repo = tmp_path / "product"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def test_profile_denies_host_reads_and_preserves_workspace_boundary(tmp_path):
    repo = checkout(tmp_path)
    config = permission_config(repo)
    profile = config["permissions"][PROFILE]
    assert profile["extends"] == ":workspace"
    assert profile["workspace_roots"] == {str(repo.resolve()): True}
    assert profile["network"] == {"enabled": False}
    filesystem = profile["filesystem"]
    assert filesystem[":root"] == "deny"
    assert filesystem[":minimal"] == "read"
    assert filesystem[str(repo.resolve())] == "write"
    assert {key for key, value in filesystem.items() if value == "write"} == {str(repo.resolve())}
    for name in (".git", ".wonjae", ".github", "AGENTS.md", "PROJECT_RULES.md"):
        assert filesystem[str(repo.resolve() / name)] == "read"
    decoded = tomllib.loads("\n".join(cli_overrides(repo)))
    assert decoded == config


def test_source_child_does_not_inherit_parent_secrets(tmp_path, monkeypatch):
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "PRODUCT_TOKEN", "RUNNER_TOKEN", "CODEX_AUTH_JSON",
                 "SECRET_ROTATION_TOKEN", "AWS_SECRET_ACCESS_KEY", "HTTP_PROXY"):
        monkeypatch.setenv(name, "synthetic-secret")
    safe = source_environment(tmp_path / "auth", Path("/nonexistent"))
    result = subprocess.run(
        [sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))"],
        env=safe, check=True, capture_output=True, text=True,
    )
    child = json.loads(result.stdout)
    assert "synthetic-secret" not in result.stdout
    assert child["CODEX_HOME"] == str(tmp_path / "auth")
    assert child["PATH"] == "/usr/local/bin:/usr/bin:/bin"


def test_rejects_non_independent_checkout(tmp_path):
    with pytest.raises(ValueError, match="independent"):
        permission_config(tmp_path)
    if os.name == "posix":
        repo = checkout(tmp_path)
        link = tmp_path / "link"
        link.symlink_to(repo, target_is_directory=True)
        with pytest.raises(ValueError, match="independent"):
            permission_config(link)


def test_trusted_missing_mounts_are_removed_on_success_and_failure(tmp_path):
    repo = checkout(tmp_path)
    original = set(repo.iterdir())
    for failing in (False, True):
        try:
            with protected_mount_placeholders(repo):
                assert (repo / ".github").is_dir()
                assert (repo / ".gitmodules").read_bytes() == b""
                if failing:
                    raise RuntimeError("synthetic turn failure")
        except RuntimeError:
            assert failing
        assert set(repo.iterdir()) == original


def test_trusted_mount_cleanup_rejects_modified_placeholder(tmp_path):
    repo = checkout(tmp_path)
    with pytest.raises(ValueError, match="placeholder was modified"):
        with protected_mount_placeholders(repo):
            (repo / ".gitmodules").write_text("unexpected")
    assert (repo / ".gitmodules").read_text() == "unexpected"


@pytest.mark.skipif(os.name != "posix", reason="symlink fixture requires Linux")
def test_protected_mount_symlink_cannot_grant_outside_reads(tmp_path):
    repo = checkout(tmp_path)
    outside = tmp_path / "other-product"
    outside.mkdir()
    (repo / ".github").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        permission_config(repo)
    with pytest.raises(ValueError, match="symlink"):
        with protected_mount_placeholders(repo):
            pytest.fail("unsafe mount entered")


def test_smoke_has_fixed_candidate_route_and_no_secret_surface():
    root = Path(__file__).resolve().parents[1]
    raw = (root / ".github/workflows/direct-worker-home-smoke.yml").read_text()
    workflow = yaml.safe_load(raw)
    assert workflow["jobs"]["isolation"]["runs-on"] == list(HOME_LABELS)
    assert "${{ secrets." not in raw
    assert "pull_request_target" not in raw
    assert "clean: false" in raw
    assert "--one-file-system" in raw
    assert "always()" in raw


def test_authenticated_smoke_is_trusted_main_only_and_has_no_product_tokens():
    root = Path(__file__).resolve().parents[1]
    raw = (root / ".github/workflows/direct-worker-home-codex-smoke.yml").read_text()
    workflow = yaml.safe_load(raw)
    job = workflow["jobs"]["source-boundary"]
    assert job["runs-on"] == list(HOME_LABELS)
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "pull_request" not in raw
    assert "PRODUCT_TOKEN" not in raw
    assert "CONTROL_READ_TOKEN" not in raw
    assert "CODEX_AUTH_JSON" in raw
    assert job["steps"][-1]["name"] == "Cleanup exact authenticated smoke storage"
