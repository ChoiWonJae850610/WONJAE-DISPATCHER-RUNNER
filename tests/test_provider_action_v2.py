import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.provider_action import prepare


def _record(project: str = "CLASSMO") -> dict:
    return {
        "schema_version": 1,
        "task_id": f"{project}-PROVIDER-OTA-001",
        "project": project,
        "attempt": 1,
        "revision": 1,
        "profile": "standard",
        "operation_type": "provider_action",
        "repository": f"ChoiWonJae850610/{project}",
        "target_branch": "cloud-dev-v1",
        "source_base_sha": "a" * 40,
        "title": "Publish preview OTA",
        "validation_workflow_path": ".github/workflows/validate.yml",
        "provider_authority": {
            "production": False,
            "new_build": False,
            "credential_mutation": False,
            "device_mutation": False,
            "destructive": False,
        },
        "provider_action": {
            "action": "eas_workflow_update",
            "working_directory": "apps/mobile",
            "workflow_file": ".eas/workflows/publish-preview.yml",
            "compatibility_base_sha": "b" * 40,
            "runtime_version": "0.0.11",
            "inputs": {"message": "Preview"},
            "parameters": {
                "environment": "preview",
                "channel": "preview",
                "platform": "ios",
                "message": "Preview",
            },
        },
    }


def test_prepare_accepts_bounded_preview_provider_record(tmp_path: Path) -> None:
    record = _record()
    record_path = tmp_path / "record.json"
    env_path = tmp_path / "env"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    prepare(
        Namespace(
            record=str(record_path),
            project="CLASSMO",
            repository="ChoiWonJae850610/CLASSMO",
            target_branch="cloud-dev-v1",
            wake_title="[PROVIDER-WAKE][DISPATCHER-V2] CLASSMO CLASSMO-PROVIDER-OTA-001 "
            + "c" * 40
            + " "
            + "a" * 40,
            control_sha="c" * 40,
            source_sha="a" * 40,
            github_env=str(env_path),
        )
    )
    env = env_path.read_text(encoding="utf-8")
    assert "PROVIDER_ACTION=eas_workflow_update" in env
    assert "PROVIDER_CHANNEL=preview" in env
    assert "PROVIDER_RUNTIME_VERSION=0.0.11" in env


def test_prepare_rejects_production_provider_authority(tmp_path: Path) -> None:
    record = _record()
    record["provider_authority"]["production"] = True
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SystemExit):
        prepare(
            Namespace(
                record=str(record_path),
                project="CLASSMO",
                repository="ChoiWonJae850610/CLASSMO",
                target_branch="cloud-dev-v1",
                wake_title="[PROVIDER-WAKE][DISPATCHER-V2] CLASSMO CLASSMO-PROVIDER-OTA-001 "
                + "c" * 40
                + " "
                + "a" * 40,
                control_sha="c" * 40,
                source_sha="a" * 40,
                github_env=str(tmp_path / "env"),
            )
        )


def test_product_provider_workflows_keep_credentials_isolated() -> None:
    mapping = {
        "CLASSMO": ("CLASSMO_WRITE_TOKEN", "CLASSMO_EXPO_TOKEN"),
        "WAFL": ("WAFL_WRITE_TOKEN", "WAFL_EXPO_TOKEN"),
        "ESC": ("ESC_WRITE_TOKEN", "ESC_EXPO_TOKEN"),
        "MUVEL": ("MUVEL_WRITE_TOKEN", "MUVEL_PROVIDER_GITHUB_TOKEN"),
    }
    all_secrets = {secret for pair in mapping.values() for secret in pair}
    for project, expected in mapping.items():
        text = Path(f".github/workflows/{project.lower()}-provider-v2.yml").read_text(
            encoding="utf-8"
        )
        assert f"name: {project} Provider v2" in text
        assert f"[PROVIDER-WAKE][DISPATCHER-V2] {project} " in text
        for secret in expected:
            assert secret in text
        for secret in all_secrets - set(expected):
            assert secret not in text


def test_provider_core_supports_common_action_families() -> None:
    text = Path(".github/workflows/provider-action-core.yml").read_text(encoding="utf-8")
    assert "eas_workflow_update" in text
    assert "eas_update" in text
    assert "github_workflow_dispatch" in text
    assert "provider_action.py check-ota" in text
    assert "source_validation_run" in text
