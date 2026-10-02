import json
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.product_patch import (
    ProductPilotError,
    load_work_order,
    patch_paths,
    validate_patch_scope,
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
        "allowed_paths": ["apps/mobile/src/components/member-shell.tsx"],
        "scope": ["Implement the bounded source slice."],
        "exclusions": ["No runtime mutation."],
        "completion_conditions": ["Exact-head validation passes."],
    }


def test_load_work_order(tmp_path: Path) -> None:
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(work_order_payload()), encoding="utf-8")
    order = load_work_order(path)
    assert order.task_id == "CLASSMO-V2-MEMBER-HOME-001"
    assert order.source_base_sha == SHA
    assert order.integration_authorized is True


def test_patch_paths_accepts_normal_unified_diff() -> None:
    patch = (
        "diff --git a/apps/mobile/src/components/member-shell.tsx "
        "b/apps/mobile/src/components/member-shell.tsx\n"
        "--- a/apps/mobile/src/components/member-shell.tsx\n"
        "+++ b/apps/mobile/src/components/member-shell.tsx\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    assert patch_paths(patch) == ("apps/mobile/src/components/member-shell.tsx",)


def test_patch_paths_rejects_rename() -> None:
    patch = (
        "diff --git a/a.ts b/a.ts\n"
        "rename from a.ts\n"
        "rename to b.ts\n"
    )
    with pytest.raises(ProductPilotError):
        patch_paths(patch)


def test_validate_patch_scope_rejects_extra_path(tmp_path: Path) -> None:
    path = tmp_path / "work-order.json"
    path.write_text(json.dumps(work_order_payload()), encoding="utf-8")
    order = load_work_order(path)
    patch = (
        "diff --git a/package-lock.json b/package-lock.json\n"
        "--- a/package-lock.json\n"
        "+++ b/package-lock.json\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    with pytest.raises(ProductPilotError):
        validate_patch_scope(patch, order)
