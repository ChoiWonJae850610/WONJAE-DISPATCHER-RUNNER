import json
import subprocess
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.product_patch import (
    ProductEdit,
    ProductPilotError,
    apply_edit_plan,
    load_work_order,
    parse_edit_plan,
    validate_branch_scope,
)

SHA = "03841079f616a8312e9b6c273a788585fa1a7b01"


def work_order_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": "CLASSMO-V2-MEMBER-HOME-001",
        "project": "CLASSMO",
        "repository": "ChoiWonJae850610/CLASSMO",
        "target_branch": "cloud-dev-v1",
        "source_base_sha": SHA,
        "title": "Productize Member Home",
        "integration_authorized": True,
        "validation_workflow_path": ".github/workflows/classmo-cloud-validation.yml",
        "required_reads": ["AGENTS.md"],
        "allowed_paths": [
            "apps/mobile/src/components/member-shell.tsx",
            "apps/mobile/src/presentation/member-home.ts",
        ],
        "required_changed_paths": [
            "apps/mobile/src/components/member-shell.tsx",
        ],
        "scope": ["Implement the bounded source slice."],
        "exclusions": ["No runtime mutation."],
        "completion_conditions": ["Exact-head validation passes."],
    }


def write_work_order(tmp_path: Path):
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(work_order_payload()), encoding="utf-8")
    return load_work_order(path)


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
        check=True,
    )


def test_load_work_order(tmp_path: Path) -> None:
    order = write_work_order(tmp_path)
    assert order.task_id == "CLASSMO-V2-MEMBER-HOME-001"
    assert order.source_base_sha == SHA
    assert order.integration_authorized is True
    assert order.required_changed_paths == (
        "apps/mobile/src/components/member-shell.tsx",
    )


def test_parse_edit_plan_accepts_bounded_replace(tmp_path: Path) -> None:
    order = write_work_order(tmp_path)
    response = json.dumps(
        {
            "edits": [
                {
                    "path": "apps/mobile/src/components/member-shell.tsx",
                    "operation": "replace",
                    "old_text": "old",
                    "new_text": "new",
                }
            ],
            "summary": "Update Member Home.",
        }
    )
    edits = parse_edit_plan(response, order)
    assert edits == (
        ProductEdit(
            path="apps/mobile/src/components/member-shell.tsx",
            operation="replace",
            old_text="old",
            new_text="new",
        ),
    )


def test_parse_edit_plan_ignores_noop_when_effective_edit_remains(tmp_path: Path) -> None:
    order = write_work_order(tmp_path)
    response = json.dumps(
        {
            "edits": [
                {
                    "path": "apps/mobile/src/components/member-shell.tsx",
                    "operation": "replace",
                    "old_text": "same",
                    "new_text": "same",
                },
                {
                    "path": "apps/mobile/src/components/member-shell.tsx",
                    "operation": "replace",
                    "old_text": "old",
                    "new_text": "new",
                },
            ],
            "summary": "Update Member Home.",
        }
    )
    edits = parse_edit_plan(response, order)
    assert len(edits) == 1
    assert edits[0].old_text == "old"
    assert edits[0].new_text == "new"


def test_parse_edit_plan_rejects_empty_edits(tmp_path: Path) -> None:
    order = write_work_order(tmp_path)
    with pytest.raises(ProductPilotError):
        parse_edit_plan(json.dumps({"edits": [], "summary": "Nothing"}), order)


def test_parse_edit_plan_rejects_failure_marker(tmp_path: Path) -> None:
    order = write_work_order(tmp_path)
    response = json.dumps(
        {
            "edits": [
                {
                    "path": "apps/mobile/src/presentation/member-home.ts",
                    "operation": "create",
                    "old_text": "",
                    "new_text": "// bwrap: sandbox failed\n",
                }
            ],
            "summary": "Blocked.",
        }
    )
    with pytest.raises(ProductPilotError):
        parse_edit_plan(response, order)


def test_parse_edit_plan_rejects_extra_path(tmp_path: Path) -> None:
    order = write_work_order(tmp_path)
    response = json.dumps(
        {
            "edits": [
                {
                    "path": "package-lock.json",
                    "operation": "replace",
                    "old_text": "old",
                    "new_text": "new",
                }
            ],
            "summary": "Unsafe.",
        }
    )
    with pytest.raises(ProductPilotError):
        parse_edit_plan(response, order)


def test_apply_edit_plan_writes_replace_and_create(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    existing = repo / "apps/mobile/src/components/member-shell.tsx"
    existing.parent.mkdir(parents=True)
    existing.write_text("before\nunique target\nafter\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)

    order = write_work_order(tmp_path)
    edits = (
        ProductEdit(
            path="apps/mobile/src/components/member-shell.tsx",
            operation="replace",
            old_text="unique target",
            new_text="updated target",
        ),
        ProductEdit(
            path="apps/mobile/src/presentation/member-home.ts",
            operation="create",
            old_text="",
            new_text="export const memberHome = true;\n",
        ),
    )
    changed = apply_edit_plan(repo, edits, order)
    assert set(changed) == {
        "apps/mobile/src/components/member-shell.tsx",
        "apps/mobile/src/presentation/member-home.ts",
    }
    assert "updated target" in existing.read_text(encoding="utf-8")


def test_apply_edit_plan_requires_unique_old_text(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    existing = repo / "apps/mobile/src/components/member-shell.tsx"
    existing.parent.mkdir(parents=True)
    existing.write_text("same\nsame\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)

    order = write_work_order(tmp_path)
    edits = (
        ProductEdit(
            path="apps/mobile/src/components/member-shell.tsx",
            operation="replace",
            old_text="same",
            new_text="different",
        ),
    )
    with pytest.raises(ProductPilotError):
        apply_edit_plan(repo, edits, order)


def test_repair_scope_accepts_required_path_from_existing_branch_history(tmp_path: Path) -> None:
    repo = tmp_path / "repo-repair"
    repo.mkdir()
    init_repo(repo)
    required = repo / "apps/mobile/src/components/member-shell.tsx"
    optional = repo / "apps/mobile/src/presentation/member-home.ts"
    required.parent.mkdir(parents=True)
    optional.parent.mkdir(parents=True)
    required.write_text("base required\n", encoding="utf-8")
    optional.write_text("base optional\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    required.write_text("task required\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "task"], check=True)

    payload = work_order_payload()
    payload["source_base_sha"] = base_sha
    path = tmp_path / "repair-work-order.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    order = load_work_order(path)

    assert validate_branch_scope(repo, order) == (
        "apps/mobile/src/components/member-shell.tsx",
    )

    edits = (
        ProductEdit(
            path="apps/mobile/src/presentation/member-home.ts",
            operation="replace",
            old_text="base optional",
            new_text="repaired optional",
        ),
    )
    changed = apply_edit_plan(
        repo,
        edits,
        order,
        require_required_paths=False,
    )
    assert changed == ("apps/mobile/src/presentation/member-home.ts",)
    validate_branch_scope(repo, order, changed)
