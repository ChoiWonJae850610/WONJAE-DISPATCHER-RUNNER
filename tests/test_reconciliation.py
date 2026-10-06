from pathlib import Path

import pytest
import yaml

from wonjae_dispatcher_runner.reconciliation import (
    ReconciliationError,
    load_claim,
    path_allowed,
)


def registry(tmp_path: Path) -> Path:
    path = tmp_path / "PROJECTS.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "projects": {
                    "WAFL": {
                        "status": "active",
                        "repository": "ChoiWonJae850610/WAFL",
                        "branch": "cloud-dev-v1",
                        "ci": {
                            "validation": {
                                "name": "WAFL Cloud Validation",
                                "path": ".github/workflows/wafl-cloud-validation.yml",
                            }
                        },
                        "execution": {"mode": "direct_worker"},
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_claim_is_registry_bound(tmp_path: Path) -> None:
    body = (
        '{"schema_version":1,"kind":"product_reconciliation","project":"WAFL",'
        '"product_pr":33,"expected_head":"' + ("a" * 40) + '"}'
    )
    claim = load_claim(registry(tmp_path), "WAFL", body)
    assert claim.repository == "ChoiWonJae850610/WAFL"
    assert claim.branch == "cloud-dev-v1"
    assert claim.product_pr == 33


def test_claim_rejects_extra_authority(tmp_path: Path) -> None:
    body = (
        '{"schema_version":1,"kind":"product_reconciliation","project":"WAFL",'
        '"product_pr":33,"expected_head":"' + ("a" * 40) + '","repository":"x/y"}'
    )
    with pytest.raises(ReconciliationError, match="shape"):
        load_claim(registry(tmp_path), "WAFL", body)


def test_reconciliation_paths_are_docs_and_state_only() -> None:
    assert path_allowed("WAFL", ".wonjae/execution-state.yaml")
    assert path_allowed("WAFL", "PROJECT_RULES.md")
    assert path_allowed("WAFL", "docs/current/10-CURRENT-BASELINE.md")
    assert path_allowed("WAFL", "supabase/stage4/README.md")
    assert not path_allowed("WAFL", "supabase/migrations/202610060002.sql")
    assert not path_allowed("CLASSMO", "src/App.tsx")
    assert not path_allowed("ESC", ".github/workflows/validate.yml")



def test_reconciliation_workflow_has_explicit_owner_replay_and_started_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    adapter = (root / ".github/workflows/reconciliation-finalizer.yml").read_text(
        encoding="utf-8"
    )
    core = (root / ".github/workflows/reconciliation-finalizer-core.yml").read_text(
        encoding="utf-8"
    )
    assert "issue_comment:" in adapter
    assert "github.event.comment.body == '[RECONCILE-RUN]'" in adapter
    assert "Record reconciliation STARTED" in core
    assert "GitHub validation wait is not an Owner action" in core
    assert '.state == "closed" and .merged_at != null' in core
    assert "ALREADY_MERGED" in core


def test_reconciliation_has_scheduled_sweeper_fallback() -> None:
    root = Path(__file__).resolve().parents[1]
    adapter = (root / ".github/workflows/reconciliation-finalizer.yml").read_text(
        encoding="utf-8"
    )
    sweeper = (root / ".github/workflows/reconciliation-sweeper.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in adapter
    assert "inputs.project == 'WAFL'" in adapter
    assert "fromJSON(inputs.issue_number)" in adapter
    assert 'cron: "*/5 * * * *"' in sweeper
    assert "push:" in sweeper
    assert "- main" in sweeper
    assert "actions: write" in sweeper
    assert "actions/workflows/reconciliation-finalizer.yml/dispatches" in sweeper
    assert 'startswith("STARTED: reconciliation finalizer run ")' in sweeper
    assert 'startswith("FAILED: reconciliation finalizer run ")' in sweeper
    assert "refusing ambiguous sweep" in sweeper
