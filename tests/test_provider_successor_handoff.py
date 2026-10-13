"""Contract tests for generic provider-success document handoff (no network calls)."""

import subprocess
from pathlib import Path

import pytest
import yaml

SCRIPT = Path("scripts/prepare_provider_successor.rb")
SHA = "a" * 40


def action(kind: str, gate: str | None = None) -> dict:
    return {
        "type": kind,
        "title": "Next Owner action",
        "source_task_id": "WAFL-NEXT-001" if kind == "SOURCE_READY" else None,
        "owner_action": "Click SANJINWORKS Owner action",
        "source_scope": ["Implement the bounded next source stage."] if kind == "SOURCE_READY" else [],
        "gate": gate,
    }


def make_state(tmp_path: Path, successor: dict | None = None) -> tuple[Path, Path]:
    product = tmp_path / "product"
    control = tmp_path / "control"
    (product / ".wonjae").mkdir(parents=True)
    control.mkdir()
    state = {
        "schema_version": 1,
        "project": "WAFL",
        "next_action": action("PROVIDER_GATE", "previous_gate"),
        "after_source_success": None,
        "source_success_queue": [],
    }
    if successor is not None:
        state["after_provider_success"] = successor
    (product / ".wonjae/execution-state.yaml").write_text(
        yaml.safe_dump(state, sort_keys=False), encoding="utf-8"
    )
    registry = {
        "projects": {
            "WAFL": {
                "status": "active",
                "repository": "ChoiWonJae850610/WAFL",
                "branch": "cloud-dev-v1",
                "execution": {
                    "mode": "direct_worker",
                    "state_path": ".wonjae/execution-state.yaml",
                    "provider_gates": {
                        "previous_gate": {
                            "approval_mode": "sanjinworks_owner_button",
                            "executor": "wafl_supabase_g1_schema_apply",
                            "production": False,
                            "destructive": False,
                        },
                        "next_gate": {
                            "approval_mode": "sanjinworks_owner_button",
                            "executor": "wafl_supabase_g1_readonly",
                            "ready_for_owner": True,
                            "production": False,
                            "destructive": False,
                        },
                    },
                },
            }
        }
    }
    (control / "PROJECTS.yaml").write_text(
        yaml.safe_dump(registry, sort_keys=False), encoding="utf-8"
    )
    return product, control


def dry_run(product: Path, control: Path, *, source_sha: str = SHA) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "ruby",
            str(SCRIPT),
            "--project",
            "WAFL",
            "--gate",
            "previous_gate",
            "--repository",
            "ChoiWonJae850610/WAFL",
            "--target-branch",
            "cloud-dev-v1",
            "--source-sha",
            source_sha,
            "--state-path",
            ".wonjae/execution-state.yaml",
            "--product-checkout",
            str(product),
            "--control-checkout",
            str(control),
            "--run-url",
            "https://github.com/example/actions/runs/123",
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_predeclared_provider_successor_becomes_state_only_owner_pr(tmp_path: Path):
    product, control = make_state(tmp_path, action("PROVIDER_GATE", "next_gate"))
    result = dry_run(product, control)
    assert result.returncode == 0
    output = yaml.safe_load(result.stdout)
    assert output["next_action"]["gate"] == "next_gate"
    assert "after_provider_success" not in output
    assert output["after_source_success"] is None
    # Dry-run must neither open a GitHub PR nor modify the product source.
    current = yaml.safe_load((product / ".wonjae/execution-state.yaml").read_text())
    assert current["next_action"]["gate"] == "previous_gate"


def test_absent_successor_is_explicit_noop(tmp_path: Path):
    product, control = make_state(tmp_path)
    result = dry_run(product, control)
    assert result.returncode == 0
    assert result.stdout.strip() == "PROVIDER_SUCCESSOR_PR=NOT_PREDECLARED"


def test_unregistered_successor_never_becomes_executable(tmp_path: Path):
    product, control = make_state(tmp_path, action("PROVIDER_GATE", "next_gate"))
    path = control / "PROJECTS.yaml"
    registry = yaml.safe_load(path.read_text())
    registry["projects"]["WAFL"]["execution"]["provider_gates"]["next_gate"][
        "ready_for_owner"
    ] = False
    path.write_text(yaml.safe_dump(registry))
    result = dry_run(product, control)
    assert result.returncode == 1
    assert "not prepared" in result.stderr


def test_provider_successor_can_reach_predeclared_source_with_terminal(tmp_path: Path):
    product, control = make_state(tmp_path, action("SOURCE_READY"))
    path = product / ".wonjae/execution-state.yaml"
    state = yaml.safe_load(path.read_text())
    state["after_source_success"] = action("PROVIDER_GATE", "next_gate")
    path.write_text(yaml.safe_dump(state, sort_keys=False))
    result = dry_run(product, control)
    assert result.returncode == 0
    output = yaml.safe_load(result.stdout)
    assert output["next_action"]["type"] == "SOURCE_READY"
    assert output["after_source_success"]["gate"] == "next_gate"


@pytest.mark.parametrize("source_sha", ["short", "z" * 40])
def test_identity_mismatch_blocks_even_dry_run(tmp_path: Path, source_sha):
    product, control = make_state(tmp_path, action("PROVIDER_GATE", "next_gate"))
    result = dry_run(product, control, source_sha=source_sha)
    assert result.returncode == 1
    assert "not prepared" in result.stderr
