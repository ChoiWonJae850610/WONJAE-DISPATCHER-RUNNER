"""Reviewed writer inventory and code pins; not an execution/approval engine."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .source_writer_fence import FenceError

WRITERS = {
    "direct_worker": ("direct_worker", [
        ".github/workflows/direct-worker.yml",
        ".github/workflows/direct-worker-core.yml", "scripts/run_direct_worker_recovery.py",
        "scripts/run_direct_worker.py", "scripts/direct_worker_route.py",
        "src/wonjae_dispatcher_runner/direct_worker.py",
        "src/wonjae_dispatcher_runner/initial_turn_recovery.py",
        "scripts/advance_execution_state.py", "src/wonjae_dispatcher_runner/execution_state.py",
    ]),
    "document_merge": ("document_merge", [
        ".github/workflows/document-pr-merge.yml",
        ".github/workflows/document-pr-merge-core.yml", "scripts/document_pr_merge.py",
    ]),
    "recovery_attach": ("source_recovery", [
        ".github/workflows/source-pr-recovery-docs.yml",
        ".github/workflows/source-pr-recovery-docs-core.yml", "scripts/attach_recovery_docs.py",
    ]),
    "recovery_prepare": ("source_recovery", [
        ".github/workflows/automatic-source-recovery-docs.yml",
        ".github/workflows/automatic-source-recovery-docs-core.yml",
        "scripts/prepare_failed_source_recovery.py",
    ]),
    "reconciliation": ("reconciliation", [
        ".github/workflows/reconciliation-finalizer.yml",
        ".github/workflows/reconciliation-finalizer-core.yml",
    ]),
    "terminal_finalization": ("source_recovery", [
        ".github/workflows/product-terminal-guard.yml",
        ".github/workflows/source-terminal-core.yml", "scripts/source_terminal.py",
        "scripts/source_pr_lifecycle.py", "src/wonjae_dispatcher_runner/source_pr_lifecycle.py",
        "src/wonjae_dispatcher_runner/source_terminal.py",
    ]),
    "provider_successor": ("provider_state_update", [
        ".github/workflows/provider-gate-approval.yml",
        ".github/workflows/provider-gate-approval-core.yml",
        "scripts/prepare_provider_successor.rb",
    ]),
    "lean_ci_stage": ("document_merge", [
        ".github/workflows/lean-ci-owner-stage.yml", "scripts/stage_lean_ci_owner_workflow.py",
        "src/wonjae_dispatcher_runner/lean_ci_owner_stage.py",
        "src/wonjae_dispatcher_runner/lean_ci_maintenance.py",
    ]),
    "lean_ci_publish": ("document_merge", [
        ".github/workflows/lean-ci-owner-publish.yml", "scripts/publish_lean_ci_owner_pr.py",
        "src/wonjae_dispatcher_runner/lean_ci_pr_publisher.py",
        "src/wonjae_dispatcher_runner/lean_ci_pr_recovery.py",
    ]),
    "legacy_source": ("direct_worker", [
        *[f".github/workflows/{p}-product-v2.yml" for p in ("wafl", "esc", "muvel")],
        ".github/workflows/classmo-product-pilot.yml", "scripts/run_product_patch.py",
        "scripts/run_product_repair.py", "src/wonjae_dispatcher_runner/product_patch.py",
    ]),
}
SHARED = [
    "src/wonjae_dispatcher_runner/source_writer_fence.py",
    "src/wonjae_dispatcher_runner/registered_source_fence.py",
    "src/wonjae_dispatcher_runner/product_git_fence.py",
    "src/wonjae_dispatcher_runner/writer_participation.py",
    "scripts/product_writer_guard.py", "scripts/source_writer_guard.py",
    "scripts/source_writer_bridge.py",
]


def code_inventory(root: Path | None = None):
    root = root or Path(__file__).resolve().parents[2]
    paths = sorted({p for _, files in WRITERS.values() for p in files} | set(SHARED))
    hashes = {p: hashlib.sha256((root / p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
              for p in paths}
    return {"writers": {k: {"role": role, "files": {p: hashes[p] for p in files}}
                        for k, (role, files) in WRITERS.items()}, "code_sha256": hashes,
            "installed": False, "meaning": "source coverage only, not deployment acceptance"}


def validate_installed_code(config: dict, root: Path | None = None):
    evidence = code_inventory(root)
    if set(config.get("installed_writer_ids", [])) != set(WRITERS):
        raise FenceError("INSTALLED_WRITER_SET_INCOMPLETE")
    if config.get("writer_code_sha256") != evidence["code_sha256"]:
        raise FenceError("INSTALLED_WRITER_CODE_MISMATCH")
    # Intentionally never sets all_participants_installed or bootstraps a store.
