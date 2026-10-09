"""Credential-free regression: reuse exactly one signed CLASSMO EAS Preview build."""

import importlib
import json
from argparse import Namespace
from pathlib import Path

import pytest

provider_action = importlib.import_module("scripts.provider_action")


SOURCE_SHA = "a" * 40
BUILD_ID = "11111111-2222-3333-4444-555555555555"


def _payload(**changes):
    base = {
        "id": BUILD_ID,
        "status": "FINISHED",
        "platform": "IOS",
        "distribution": "INTERNAL",
        "buildProfile": "preview",
        "appIdentifier": "com.sanjinworks.classmo",
        "appVersion": "0.0.12",
        "appBuildVersion": "4",
        "gitCommitHash": SOURCE_SHA,
        "runtime": {"version": "0.0.12"},
        "updateChannel": {"name": "preview"},
        "isForIosSimulator": False,
        "artifacts": {"buildUrl": "https://example.invalid/signed-private-archive.ipa"},
    }
    return {**base, **changes}


def _run(tmp_path: Path, builds, phase: str = "preflight", expected_id: str | None = None):
    payload = tmp_path / "list.json"
    env_file = tmp_path / "env"
    payload.write_text(json.dumps(builds), encoding="utf-8")
    provider_action.parse_classmo_build_list(
        Namespace(
            input=str(payload),
            phase=phase,
            source_sha=SOURCE_SHA,
            app_version="0.0.12",
            build_number="4",
            runtime_version="0.0.12",
            channel="preview",
            expected_id=expected_id,
            github_env=str(env_file),
        )
    )
    return env_file.read_text(encoding="utf-8")


def test_existing_exact_build_is_reused_instead_of_created(tmp_path: Path):
    env = _run(tmp_path, [_payload()])
    assert "EAS_EXISTING_BUILD=1" in env
    assert f"EAS_BUILD_ID={BUILD_ID}" in env
    assert "PROVIDER_STATUS=SUCCESS" in env
    assert "EAS_BUILD_ARCHIVE_PRESENT=1" in env
    assert "signed-private-archive" not in env


def test_empty_first_build_list_permits_only_separately_owner_authorized_launch(tmp_path: Path):
    assert _run(tmp_path, []) == "EAS_EXISTING_BUILD=0\n"


def test_final_readback_requires_exact_id_and_metadata(tmp_path: Path):
    env = _run(tmp_path, [_payload()], phase="verify", expected_id=BUILD_ID)
    assert "EAS_BUILD_ARCHIVE_PRESENT=1" in env
    assert "EAS_EXISTING_BUILD" not in env
    with pytest.raises(SystemExit, match="original approved build"):
        _run(
            tmp_path,
            [_payload()],
            phase="verify",
            expected_id="bbbbbbbb-2222-3333-4444-555555555555",
        )
    with pytest.raises(SystemExit, match="exact existing EAS"):
        _run(tmp_path, [_payload()], phase="verify")


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "ERRORED"},
        {"status": "IN_PROGRESS"},
        {"gitCommitHash": "b" * 40},
        {"gitCommitHash": None},
        {"distribution": "STORE"},
        {"platform": "ANDROID"},
        {"appVersion": "0.0.11"},
        {"appBuildVersion": "3"},
        {"buildProfile": "production"},
        {"appIdentifier": "com.other.app"},
        {"runtime": {"version": "0.0.11"}},
        {"runtime": None},
        {"updateChannel": {"name": "other"}},
        {"isForIosSimulator": True},
        {"isForIosSimulator": None},
        {"artifacts": {}},
        {"artifacts": {"buildUrl": "http://insecure.invalid/build.ipa"}},
    ],
)
def test_ambiguous_incorrect_or_unfinished_prior_build_never_rebuilds(tmp_path: Path, changes):
    with pytest.raises(SystemExit):
        _run(tmp_path, [_payload(**changes)])
    assert not (tmp_path / "env").exists()


def test_two_matching_ids_are_ambiguous_and_fail_closed(tmp_path: Path):
    with pytest.raises(SystemExit, match="multiple"):
        _run(
            tmp_path,
            [_payload(), _payload(id="aaaaaaaa-2222-3333-4444-555555555555")],
        )


@pytest.mark.parametrize("invalid", [{}, None, "bad", {"currentPage": []}, [None]])
def test_malformed_provider_list_must_not_authorize_another_build(tmp_path: Path, invalid):
    with pytest.raises(SystemExit):
        _run(tmp_path, invalid)


def test_workflow_exact_source_preflight_precedes_any_build_and_reuses_on_match():
    raw = Path(".github/workflows/provider-gate-approval-core.yml").read_text(
        encoding="utf-8"
    )
    start = raw.index("name: CLASSMO · read-only existing Preview build preflight")
    actual_build = raw.index("name: CLASSMO · build signed internal preview")
    verify = raw.index("name: CLASSMO · verify signed preview build result")
    wafl = raw.index("name: WAFL · build signed internal Preview with fabric W")
    assert start < actual_build < verify < wafl
    preflight = raw[start:actual_build]
    launch = raw[actual_build:verify]
    readback = raw[verify:wafl]
    for marker in (
        'test "$(git rev-parse HEAD)" = "$SOURCE_SHA"',
        "eas build:list",
        "--app-version",
        "--app-build-version",
        "parse-classmo-build-list",
        "--phase preflight",
    ):
        assert marker in preflight
    assert 'if [ "${EAS_EXISTING_BUILD:-0}" = "1" ]; then' in launch
    assert 'test -n "${EAS_BUILD_ID:-}"' in launch
    assert "--freeze-credentials" in launch
    assert "eas build \\" in launch
    assert "eas build:list" in readback
    assert "eas build:view" not in readback
    assert "--phase verify" in readback
    assert '--expected-id "$EAS_BUILD_ID"' in readback
    assert 'test "$EAS_BUILD_ARCHIVE_PRESENT" = "1"' in readback
    assert "eas build \\" not in readback
    assert "eas update " not in readback
