from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from wonjae_dispatcher_runner.home_isolation import (
    HOME_LABELS,
    PROFILE,
    cli_overrides,
    permission_config,
    source_environment,
)


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
