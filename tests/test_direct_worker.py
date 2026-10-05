from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.direct_worker import (
    DirectWorkerError,
    changed_paths,
    git_metadata_snapshot,
    load_direct_worker_route,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def registry(tmp_path: Path, mode: str = "direct_worker") -> Path:
    path = tmp_path / "PROJECTS.yaml"
    path.write_text(
        f"""
schema_version: 1
projects:
  ESC:
    status: active
    repository: ChoiWonJae850610/ESC
    branch: cloud-dev-v1
    startup_entry: AGENTS.md
    project_rules: PROJECT_RULES.md
    canonical_docs:
      - docs/NEXT_WORK.md
      - docs/PRODUCT_SPEC.md
    ci:
      validation:
        name: Validate ESC
        path: .github/workflows/validate.yml
    execution:
      mode: {mode}
      source_writer_concurrency: 1
      cross_project_parallel: true
      runner_repository: ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER
      runner_workflow: .github/workflows/direct-worker.yml
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def test_direct_worker_route_is_registry_driven(tmp_path):
    route = load_direct_worker_route(registry(tmp_path), "ESC")
    assert route.repository == "ChoiWonJae850610/ESC"
    assert route.branch == "cloud-dev-v1"
    assert route.canonical_docs == ("docs/NEXT_WORK.md", "docs/PRODUCT_SPEC.md")


def test_direct_worker_rejects_legacy_or_excluded_routes(tmp_path):
    with pytest.raises(DirectWorkerError, match="not registered"):
        load_direct_worker_route(registry(tmp_path, "dispatcher_v2_legacy_active"), "ESC")
    with pytest.raises(DirectWorkerError, match="KDN"):
        load_direct_worker_route(registry(tmp_path), "KDN")


def test_changed_paths_reads_real_worktree_and_rejects_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "worker@example.invalid")
    git(repo, "config", "user.name", "Direct Worker Test")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "base")
    (repo / "a.txt").write_text("b\n", encoding="utf-8")
    (repo / "new.txt").write_text("n\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    assert set(changed_paths(repo)) == {"a.txt", "new.txt"}

    outside = tmp_path / "outside.txt"
    outside.write_text("x\n", encoding="utf-8")
    (repo / "link.txt").symlink_to(outside)
    with pytest.raises(DirectWorkerError, match="symlink|escapes"):
        changed_paths(repo)


def test_git_metadata_snapshot_detects_protected_mutation(tmp_path):
    repo = tmp_path / "repo-meta"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "worker@example.invalid")
    git(repo, "config", "user.name", "Direct Worker Test")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-m", "base")

    before = git_metadata_snapshot(repo)
    git(repo, "config", "core.autocrlf", "false")
    assert git_metadata_snapshot(repo) != before


def test_changed_paths_rejects_protected_authority_files(tmp_path):
    repo = tmp_path / "repo-protected"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "worker@example.invalid")
    git(repo, "config", "user.name", "Direct Worker Test")
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    git(repo, "add", "AGENTS.md")
    git(repo, "commit", "-m", "base")

    (repo / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(DirectWorkerError, match="protected source path"):
        changed_paths(repo)
