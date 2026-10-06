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
