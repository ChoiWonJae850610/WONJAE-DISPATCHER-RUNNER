from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.direct_worker import (
    DirectWorkerError,
    _prompt,
    changed_paths,
    git_metadata_snapshot,
    load_direct_worker_route,
    require_next_source_ready,
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


def registry_with_handoff(
    tmp_path: Path,
    action_type: str,
    current_head: str,
) -> Path:
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir(parents=True)
    (handoffs / "ESC.yaml").write_text(
        f"""
schema_version: 1
project: ESC
updated_at: "2026-10-06T08:38:00+09:00"
repository: ChoiWonJae850610/ESC
current_branch: cloud-dev-v1
current_head: {current_head}
source_validation:
  result: PASS
  workflow: Validate ESC
  run_id: 123
last_completed_source:
  id: ESC-OLD
  title: Old task
  pr: 1
  integrated_sha: {current_head}
  validation_run_id: 123
next_action:
  type: {action_type}
  title: Current handoff task
  source_task_id: ESC-NEXT
  owner_action: null
  source_scope:
    - Make the bounded source change.
""".strip()
        + "\n",
        encoding="utf-8",
    )
    path = registry(tmp_path)
    content = path.read_text(encoding="utf-8")
    content = content.replace(
        "      runner_workflow: .github/workflows/direct-worker.yml\n",
        "      runner_workflow: .github/workflows/direct-worker.yml\n"
        "      handoff_path: handoffs/ESC.yaml\n",
    )
    path.write_text(content, encoding="utf-8")
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


def test_handoff_gates_next_source_work(tmp_path):
    sha = "a" * 40
    route = load_direct_worker_route(
        registry_with_handoff(tmp_path, "SOURCE_READY", sha),
        "ESC",
    )
    assert route.handoff is not None
    assert route.handoff.next_action_title == "Current handoff task"
    require_next_source_ready(route, sha)

    with pytest.raises(DirectWorkerError, match="stale"):
        require_next_source_ready(route, "b" * 40)

    blocked = load_direct_worker_route(
        registry_with_handoff(tmp_path / "manual", "MANUAL_QA", sha),
        "ESC",
    )
    with pytest.raises(DirectWorkerError, match="MANUAL_QA"):
        require_next_source_ready(blocked, sha)


def test_source_ready_prompt_makes_handoff_sequencing_authoritative(tmp_path):
    sha = "a" * 40
    route = load_direct_worker_route(
        registry_with_handoff(tmp_path, "SOURCE_READY", sha),
        "ESC",
    )
    prompt = _prompt(route, "next", sha, "")
    assert "trusted Runner has already read and validated" in prompt
    assert "execution-routing authority" in prompt
    assert "supersede older task-state" in prompt
    assert "SOURCE_READY handoff explicitly authorizes source-only schema/migration FILE" in prompt
    assert "never authorizes applying that migration to a live provider" in prompt
    assert "Execute exactly the SOURCE_READY handoff task: Current handoff task" in prompt
    assert "Trusted Runner execution handoff snapshot" in prompt
    assert f"- exact current_head: {sha}" in prompt
    assert "- source validation: PASS (run 123)" in prompt
    assert "- next_action.type: SOURCE_READY" in prompt
    assert "not available inside this network-disabled sandbox" in prompt
    assert "do not require or" in prompt
    assert "attempt a second handoff read" in prompt


def test_direct_worker_workflow_prepares_linux_user_namespaces():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/direct-worker-core.yml").read_text(
        encoding="utf-8"
    )
    assert "Prepare Linux sandbox prerequisites" in workflow
    assert "kernel.unprivileged_userns_clone=1" in workflow
    assert "kernel.apparmor_restrict_unprivileged_userns=0" in workflow
    assert "unshare -Urn true" in workflow
