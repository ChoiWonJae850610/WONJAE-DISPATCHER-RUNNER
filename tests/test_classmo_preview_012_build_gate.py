"""Credential-free guard tests for the Owner-selected CLASSMO 0.0.12 (4) Preview build."""
from pathlib import Path

import pytest
import yaml

CORE = Path(".github/workflows/provider-gate-approval-core.yml")
CALLER = Path(".github/workflows/provider-gate-approval.yml")


def test_classmo_012_gate_accepts_only_new_version_build_and_runtime():
    workflow = CORE.read_text(encoding="utf-8")
    block = workflow.split('elsif executor == "classmo_eas_build"', 1)[1].split(
        'elsif executor == "wafl_eas_build"', 1
    )[0]
    for exact in (
        '"provider" => "eas_build"',
        '"working_directory" => "apps/mobile"',
        '"platform" => "ios"',
        '"profile" => "preview"',
        '"app_version" => "0.0.12"',
        '"build_number" => "4"',
        '"runtime_version" => "0.0.12"',
        '"environment" => "preview"',
        '"channel" => "preview"',
        '"PROVIDER_RUNTIME_VERSION" => config["runtime_version"]',
        '"PROVIDER_CHANNEL" => config["channel"]',
        '"PROVIDER_ENVIRONMENT" => config["environment"]',
    ):
        assert exact in block
    assert '"app_version" => "0.0.11"' not in block
    assert '"build_number" => "3"' not in block


def test_preview_build_source_is_exact_and_prohibits_ota_or_credential_mutation():
    workflow = CORE.read_text(encoding="utf-8")
    verifier = workflow.split("name: CLASSMO · verify preview build source contract", 1)[1]
    verifier = verifier.split("name: EAS · set up EAS CLI", 1)[0]
    for check in (
        'app.get("version") != os.environ["PROVIDER_APP_VERSION"]',
        'str(app.get("ios", {}).get("buildNumber")) != os.environ["PROVIDER_BUILD_NUMBER"]',
        'app.get("version") != os.environ["PROVIDER_RUNTIME_VERSION"]',
        'app.get("runtimeVersion") != {"policy": "appVersion"}',
        'app.get("ios", {}).get("bundleIdentifier") != "com.sanjinworks.classmo"',
        'eas.get("cli", {}).get("appVersionSource") != "local"',
        'preview.get("environment") != os.environ["PROVIDER_ENVIRONMENT"]',
        'preview.get("channel") != os.environ["PROVIDER_CHANNEL"]',
        'preview.get("autoIncrement") is not None',
        'preview.get("ios", {}).get("simulator") is not False',
    ):
        assert check in verifier
    assert "PROVIDER_EXECUTOR == 'classmo_eas_build'" in workflow
    assert "--freeze-credentials" in workflow
    assert 'test "$PROVIDER_STATUS" = "SUCCESS"' in workflow
    assert 'test "$EAS_BUILD_APP_VERSION" = "$PROVIDER_APP_VERSION"' in workflow
    assert 'test "$EAS_BUILD_APP_BUILD_VERSION" = "$PROVIDER_BUILD_NUMBER"' in workflow
    assert 'test "$EAS_BUILD_DISTRIBUTION" = "internal"' in workflow


def test_owner_only_caller_preserves_classmo_scope_and_existing_legacy_ota():
    caller = CALLER.read_text(encoding="utf-8")
    workflow = CORE.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in caller
    assert "CLASSMO_EXPO_TOKEN" in caller
    assert 'project: CLASSMO' in caller
    assert 'state_path: .wonjae/execution-state.yaml' in caller
    ota = workflow.split('elsif executor == "classmo_eas_update"', 1)[1].split(
        'elsif executor == "wafl_eas_update"', 1
    )[0]
    assert '"runtime_version" => "0.0.11"' in ota
    assert '"compatibility_base_sha" => "bc18af6cab37408643f3825e1137a75dd4b899f3"' in ota
