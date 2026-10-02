import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "provider_action.py"
SPEC = importlib.util.spec_from_file_location("provider_action_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
prepare = MODULE.prepare


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


def test_provider_evidence_uses_printf_not_shell_backtick_substitution() -> None:
    text = Path(".github/workflows/provider-action-core.yml").read_text(encoding="utf-8")
    assert 'echo "- task_id: `$TASK_ID`"' not in text
    assert "printf -- '- task_id: `%s`\\n' \"$TASK_ID\"" in text


def test_eas_workflow_uses_exact_local_checkout_without_ref_lookup() -> None:
    text = Path(".github/workflows/provider-action-core.yml").read_text(encoding="utf-8")
    section = text.split("EAS · run repository workflow update", 1)[1].split(
        "EAS · run direct compatible update", 1
    )[0]
    assert 'test "$(git rev-parse HEAD)" = "$SOURCE_SHA"' in section
    assert '--ref "$SOURCE_SHA"' not in section
    assert 'eas workflow:run "$PROVIDER_WORKFLOW_FILE"' in section


def test_async_provider_launch_does_not_wait_for_external_queue() -> None:
    text = Path(".github/workflows/provider-action-core.yml").read_text(encoding="utf-8")
    eas_section = text.split("EAS · run repository workflow update", 1)[1].split(
        "EAS · run direct compatible update", 1
    )[0]
    github_section = text.split("GitHub provider · dispatch", 1)[1].split(
        "RESULT · record queued external provider", 1
    )[0]
    assert "--wait" not in eas_section
    assert "gh run watch" not in github_section
    assert "## Dispatcher v2 provider QUEUED evidence" in text


def test_provider_status_workflow_reads_existing_run_without_relaunch() -> None:
    text = Path(".github/workflows/provider-status-core.yml").read_text(encoding="utf-8")
    assert "eas workflow:status" in text
    assert "eas workflow:run" not in text
    assert "extract-provider-run" in text
    assert "## Dispatcher v2 provider completion evidence" in text


def test_product_provider_adapters_support_owner_status_comment() -> None:
    for project in ("classmo", "wafl", "esc", "muvel"):
        text = Path(f".github/workflows/{project}-provider-v2.yml").read_text(encoding="utf-8")
        assert "issue_comment:" in text
        assert "github.event.comment.body == '[PROVIDER-STATUS]'" in text
        assert "provider-status-core.yml" in text


def test_provider_status_installs_app_deps_only_for_eas_config_resolution() -> None:
    text = Path(".github/workflows/provider-status-core.yml").read_text(encoding="utf-8")
    assert "EAS · install app dependencies for config resolution" in text
    assert "npm ci --ignore-scripts --no-audit --no-fund" in text
    assert "eas workflow:status" in text


def test_parse_update_group_accepts_eas_list_formatted_message(tmp_path: Path) -> None:
    payload = {
        "currentPage": [
            {
                "branch": "preview",
                "message": '"CLASSMO 0.0.11 Core UX preview" (5 hours ago by robot)',
                "runtimeVersion": "0.0.11",
                "group": "c470fd4f-cb97-4fba-9a41-e55f7a00115b",
                "platforms": "ios",
            }
        ]
    }
    input_path = tmp_path / "update-list.json"
    env_path = tmp_path / "env"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    MODULE.parse_update_group(
        Namespace(
            input=str(input_path),
            message="CLASSMO 0.0.11 Core UX preview",
            runtime_version="0.0.11",
            platform="ios",
            github_env=str(env_path),
        )
    )
    assert (
        "EAS_UPDATE_GROUP_ID=c470fd4f-cb97-4fba-9a41-e55f7a00115b"
        in env_path.read_text(encoding="utf-8")
    )


def test_parse_update_view_binds_platform_update_to_exact_group(tmp_path: Path) -> None:
    payload = [
        {
            "id": "01a0fd70-1111-2222-3333-444444444444",
            "group": "c470fd4f-cb97-4fba-9a41-e55f7a00115b",
            "branch": "preview",
            "message": "CLASSMO 0.0.11 Core UX preview",
            "runtimeVersion": "0.0.11",
            "platform": "ios",
        }
    ]
    input_path = tmp_path / "update-view.json"
    env_path = tmp_path / "env"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    MODULE.parse_update_view(
        Namespace(
            input=str(input_path),
            group_id="c470fd4f-cb97-4fba-9a41-e55f7a00115b",
            message="CLASSMO 0.0.11 Core UX preview",
            runtime_version="0.0.11",
            platform="ios",
            github_env=str(env_path),
        )
    )
    env = env_path.read_text(encoding="utf-8")
    assert "EAS_UPDATE_ID=01a0fd70-1111-2222-3333-444444444444" in env
    assert "EAS_UPDATE_GROUP_ID=c470fd4f-cb97-4fba-9a41-e55f7a00115b" in env


def test_provider_readback_uses_update_group_then_update_view() -> None:
    for workflow in (
        ".github/workflows/provider-action-core.yml",
        ".github/workflows/provider-status-core.yml",
    ):
        text = Path(workflow).read_text(encoding="utf-8")
        assert "parse-update-group" in text
        assert 'eas update:view "$EAS_UPDATE_GROUP_ID"' in text
        assert "parse-update-view" in text


def test_provider_status_splits_group_and_update_readback_across_steps() -> None:
    text = Path(".github/workflows/provider-status-core.yml").read_text(encoding="utf-8")
    group_index = text.index("EAS · read update group after success")
    update_index = text.index("EAS · read platform update after group resolution")
    assert group_index < update_index
    group_section = text[group_index:update_index]
    assert "parse-update-group" in group_section
    assert "eas update:view" not in group_section
    update_section = text[update_index:]
    assert 'test -n "${EAS_UPDATE_GROUP_ID:-}"' in update_section
    assert 'eas update:view "$EAS_UPDATE_GROUP_ID"' in update_section
    assert "parse-update-view" in update_section
