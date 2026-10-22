"""Credential-free exact ESC signed EAS build readback and identity boundary."""

import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "provider_action.py"
SPEC = importlib.util.spec_from_file_location("esc_provider_action_readback", MODULE)
assert SPEC is not None and SPEC.loader is not None
provider_action = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider_action)

SHA = "a" * 40
BUILD_ID = "11111111-2222-3333-4444-555555555555"


def _readback(tmp_path: Path, builds, *, identifier="com.sanjinworks.esc", phase="preflight"):
    path = tmp_path / "build-list.json"
    env = tmp_path / "env"
    path.write_text(json.dumps(builds), encoding="utf-8")
    provider_action.parse_classmo_build_list(
        Namespace(
            input=str(path),
            phase=phase,
            source_sha=SHA,
            app_version="0.1.1",
            build_number="2",
            runtime_version="0.1.1",
            channel="preview",
            app_identifier=identifier,
            expected_id=BUILD_ID if phase == "verify" else None,
            github_env=str(env),
        )
    )
    return env.read_text(encoding="utf-8")


def _build(identifier="com.sanjinworks.esc"):
    return {
        "id": BUILD_ID,
        "status": "FINISHED",
        "platform": "IOS",
        "distribution": "INTERNAL",
        "buildProfile": "preview",
        "appIdentifier": identifier,
        "appVersion": "0.1.1",
        "appBuildVersion": "2",
        "gitCommitHash": SHA,
        "runtime": {"version": "0.1.1"},
        "updateChannel": {"name": "preview"},
        "isForIosSimulator": False,
        "artifacts": {"applicationArchiveUrl": "https://example.invalid/signed.ipa"},
    }


def test_new_esc_build_preflight_may_launch_once(tmp_path):
    assert _readback(tmp_path, []) == "EAS_EXISTING_BUILD=0\n"


def test_exact_esc_preview_build_is_reused_and_verified(tmp_path):
    assert "EAS_EXISTING_BUILD=1" in _readback(tmp_path, [_build()])
    assert "EAS_BUILD_ARCHIVE_PRESENT=1" in _readback(
        tmp_path, [_build()], phase="verify"
    )


@pytest.mark.parametrize("identifier", ["com.sanjinworks.classmo", "com.other.app"])
def test_esc_rejects_cross_product_or_wrong_bundle(tmp_path, identifier):
    with pytest.raises(SystemExit):
        _readback(tmp_path, [_build(identifier)])
    assert not (tmp_path / "env").exists()


def test_esc_cannot_impersonate_classmo_or_skip_allowed_app_ids(tmp_path):
    with pytest.raises(SystemExit, match="application identifier"):
        _readback(tmp_path, [], identifier="com.other.app")
    with pytest.raises(SystemExit, match="appIdentifier"):
        _readback(tmp_path, [_build()], identifier="com.sanjinworks.classmo")
