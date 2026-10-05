import json
import subprocess
import threading
from types import SimpleNamespace

import pytest

from wonjae_dispatcher_runner import product_patch as patch
from wonjae_dispatcher_runner.repair_timeout import repair_plan_deadline

CAPACITY = "Selected model is at capacity. Please try a different model."


def fixture(tmp_path, mode, path="source.txt"):
    repo = tmp_path / "product"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    (repo / "AGENTS.md").write_text("Synthetic scope\n")
    (repo / path).write_text("base\n")
    git("add", ".")
    git("commit", "-qm", "source base")
    base = git("rev-parse", "HEAD")
    if mode == "repair":
        (repo / path).write_text("initial task\n")
        git("commit", "-qam", "initial task")
    head = git("rev-parse", "HEAD")
    work = tmp_path / "work.json"
    work.write_text(json.dumps({
        "schema_version": 1, "task_id": "CLASSMO-SYNTHETIC-001", "project": "CLASSMO",
        "repository": "owner/product", "target_branch": "main", "source_base_sha": base,
        "title": "Synthetic plan recovery", "integration_authorized": True,
        "validation_workflow_path": ".github/workflows/test.yml",
        "required_reads": ["AGENTS.md"], "allowed_paths": [path],
        "required_changed_paths": [path], "scope": ["Update source"],
        "exclusions": ["No provider work"], "completion_conditions": ["Exact validation"],
    }))
    return repo, work, git, head


@pytest.mark.parametrize("mode", ["patch", "repair"])
@pytest.mark.parametrize("scenario", [
    "capacity_then_complete", "transport_then_complete", "capacity_exhausted",
    "mixed_exhausted", "unknown_error", "usage_limit", "missing_account", "mutation",
    "timeout_then_complete",
])
def test_readonly_failures_use_one_shared_budget_and_fresh_sessions(
    tmp_path, monkeypatch, mode, scenario,
):
    repo, work, git, head = fixture(tmp_path, mode)
    sessions, closed, delays = [], [], []
    monkeypatch.setattr(patch, "sleep", delays.append)
    monkeypatch.setattr(patch, "repair_plan_deadline", lambda: repair_plan_deadline(0.2))

    class Codex:
        def __init__(self, **kwargs):
            assert len(sessions) == len(closed)
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(self)

        def account(self, **kwargs):
            return SimpleNamespace(account=None if scenario == "missing_account" else object())

        def thread_start(self, **kwargs):
            assert kwargs["sandbox"] == patch.Sandbox.read_only
            assert kwargs["approval_mode"] == patch.ApprovalMode.deny_all
            assert kwargs["ephemeral"] is True
            return self

        def run(self, prompt, **kwargs):
            assert git("rev-parse", "HEAD") == head and git("status", "--porcelain") == ""
            attempt = len(sessions)
            if scenario == "timeout_then_complete" and attempt == 1:
                threading.Event().wait(1)  # Actual Linux deadline interrupts this turn.
            if scenario == "unknown_error":
                raise RuntimeError("unsupported model")
            if scenario == "usage_limit":
                raise RuntimeError("usage limit reached")
            if scenario == "mutation":
                (repo / "outside.txt").write_text("unexpected")
                raise RuntimeError(CAPACITY)
            if scenario == "mixed_exhausted" and attempt == 1:
                return SimpleNamespace(status="completed", error=None,
                                       final_response='{"edits":[],"summary":"invalid"}')
            if scenario in {"capacity_exhausted", "mixed_exhausted"} or (
                scenario == "capacity_then_complete" and attempt == 1
            ):
                raise RuntimeError(CAPACITY)
            if scenario == "transport_then_complete" and attempt == 1:
                raise TimeoutError("the SDK read operation timed out")
            return SimpleNamespace(status="completed", error=None, final_response=json.dumps({
                "edits": [{"path": "source.txt", "operation": "write",
                           "old_text": "", "new_text": "complete\n"}],
                "summary": "Synthetic complete plan",
            }))

    monkeypatch.setattr(patch, "Codex", Codex)
    args = (repo, work, "a" * 40, tmp_path / "auth")
    execute = (lambda: patch.generate_and_apply_product_patch(*args)) if mode == "patch" else (
        lambda: patch.generate_and_apply_product_repair(*args, "Synthetic test failed")
    )
    if scenario.endswith("then_complete"):
        _, changed = execute()
        assert changed == ("source.txt",) and len(sessions) == 2
        assert (repo / "source.txt").read_text() == "complete\n"
    else:
        with pytest.raises(RuntimeError):
            execute()
        expected = 3 if scenario in {"capacity_exhausted", "mixed_exhausted"} else 1
        assert len(sessions) == expected
        if scenario != "mutation":
            assert git("status", "--porcelain") == ""
    assert sessions == closed
    assert git("rev-parse", "HEAD") == head
    assert all(delay in {10, 20} for delay in delays) and sum(delays) <= 30
    if scenario in {"unknown_error", "usage_limit", "missing_account", "mutation"}:
        assert delays == []


@pytest.mark.parametrize("path", ["한글.txt", "file with space.txt", "a -> b.txt"])
def test_git_scope_paths_are_exact_unquoted_and_required_cumulative(tmp_path, path):
    repo, work, git, _ = fixture(tmp_path, "patch", path)
    order = patch.load_work_order(work)
    changed = patch.apply_edit_plan(
        repo, (patch.ProductEdit(path, "write", "", "task\n"),), order,
    )
    assert changed == (path,)
    assert patch.validate_branch_scope(repo, order) == (path,)
    git("add", ".")
    git("commit", "-qm", "task")
    assert patch.branch_diff_paths(repo, order.source_base_sha) == (path,)
    (repo / "다른 파일.txt").write_text("outside")
    with pytest.raises(patch.ProductPilotError, match="outside"):
        patch.validate_branch_scope(repo, order)


def test_real_rename_is_still_rejected(tmp_path):
    repo, _, git, _ = fixture(tmp_path, "patch")
    git("mv", "source.txt", "renamed.txt")
    with pytest.raises(patch.ProductPilotError, match="renames"):
        patch.changed_paths(repo)
