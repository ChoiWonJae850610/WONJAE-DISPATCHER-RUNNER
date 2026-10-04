import json
import subprocess
from types import SimpleNamespace

import pytest

from wonjae_dispatcher_runner import product_patch as patch


@pytest.mark.parametrize("revert_required", [False, True])
def test_initial_fail_repair_one_fail_repair_two_pass(tmp_path, monkeypatch, revert_required):
    repo = tmp_path / "product"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    (repo / "AGENTS.md").write_text("bounded synthetic task\n")
    (repo / "required.txt").write_text("base\n")
    (repo / "optional.txt").write_text("FAIL initial\n")
    git("add", ".")
    git("commit", "-qm", "source base")
    base = git("rev-parse", "HEAD")
    work = tmp_path / "work.json"
    work.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": "CLASSMO-REPAIR-001",
                "project": "CLASSMO",
                "repository": "owner/product",
                "target_branch": "main",
                "source_base_sha": base,
                "title": "Synthetic bounded recovery",
                "integration_authorized": True,
                "validation_workflow_path": ".github/workflows/validate.yml",
                "required_reads": ["AGENTS.md"],
                "allowed_paths": ["required.txt", "optional.txt"],
                "required_changed_paths": ["required.txt"],
                "scope": ["Fix synthetic validation"],
                "exclusions": ["No provider action"],
                "completion_conditions": ["Validation PASS"],
            }
        )
    )
    order = patch.load_work_order(work)
    (repo / "required.txt").write_text("task\n")
    git("add", ".")
    git("commit", "-qm", "initial task")
    edits_one = [
        {
            "path": "optional.txt",
            "operation": "write",
            "old_text": "",
            "new_text": "FAIL repair 1\n",
        }
    ]
    if revert_required:
        edits_one.append(
            {"path": "required.txt", "operation": "write", "old_text": "", "new_text": "base\n"}
        )
    edits_two = [
        {
            "path": "optional.txt",
            "operation": "write",
            "old_text": "",
            "new_text": "PASS repair 2\n",
        }
    ]
    if revert_required:
        edits_two.append(
            {
                "path": "required.txt",
                "operation": "write",
                "old_text": "",
                "new_text": "task restored\n",
            }
        )
    responses = iter([edits_one, edits_two])

    class Codex:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def account(self, **kwargs):
            return SimpleNamespace(account=object())

        def thread_start(self, **kwargs):
            return self

        def run(self, *args, **kwargs):
            return SimpleNamespace(
                status="completed",
                error=None,
                final_response=json.dumps(
                    {"edits": next(responses), "summary": "synthetic repair"}
                ),
            )

    monkeypatch.setattr(patch, "Codex", Codex)
    outcomes = ["FAIL" if "FAIL" in (repo / "optional.txt").read_text() else "PASS"]
    for number in (1, 2):
        _, changed = patch.generate_and_apply_product_repair(
            repo, work, "a" * 40, tmp_path / "auth", f"validation FAIL before repair {number}"
        )
        assert changed
        assert set(changed) <= set(order.allowed_paths)
        git("add", ".")
        git("commit", "-qm", f"repair {number}")
        outcomes.append("FAIL" if "FAIL" in (repo / "optional.txt").read_text() else "PASS")
        if number == 1 and revert_required:
            with pytest.raises(patch.ProductPilotError, match="required changed paths"):
                patch.validate_branch_scope(repo, order)
    assert outcomes == ["FAIL", "FAIL", "PASS"]
    assert "required.txt" in patch.validate_branch_scope(repo, order)
    if not revert_required:
        # Neither repair rewrote the required file; the task's cumulative diff satisfies it.
        assert git("diff", "--name-only", "HEAD~1", "HEAD") == "optional.txt"


def test_final_scope_gate_uses_current_worktree_not_historical_union(tmp_path):
    repo = tmp_path / "reverted"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    (repo / "required.txt").write_text("base\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (repo / "required.txt").write_text("changed\n")
    git("add", ".")
    git("commit", "-qm", "task")
    (repo / "required.txt").write_text("base\n")
    order = SimpleNamespace(
        source_base_sha=base,
        allowed_paths=("required.txt",),
        required_changed_paths=("required.txt",),
    )
    with pytest.raises(patch.ProductPilotError, match="required changed paths"):
        patch.validate_branch_scope(repo, order, ("required.txt",))
