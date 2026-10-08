"""Fail-closed regression for the audited CLASSMO 019 JS-only OTA exception."""

import copy
import importlib.util
import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "provider_action.py"
SPEC = importlib.util.spec_from_file_location("provider_action_ota_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

NEW_JS_DEPENDENCIES = {
    "@hookform/resolvers": "5.9.1",
    "react-hook-form": "7.89.0",
    "zod": "4.6.5",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _approved_lock_additions() -> dict:
    result = {}
    for path, (version, resolved, integrity) in MODULE.CLASSMO_019_JS_LOCK_PACKAGES.items():
        entry = {
            "version": version,
            "resolved": resolved,
            "integrity": integrity,
            "license": "MIT",
        }
        if path == "apps/mobile/node_modules/@hookform/resolvers":
            entry["dependencies"] = {"@standard-schema/utils": "^0.3.0"}
        result[path] = entry
    return result


def _repository(tmp_path: Path, mutation: str = "valid") -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Synthetic Test")
    _git(repo, "config", "user.email", "synthetic@example.invalid")

    original_dependencies = {
        "@classmo/domain": "*",
        "expo": "~57.0.25",
        "expo-router": "~57.0.23",
        "react": "19.2.3",
        "react-native": "0.86.3",
    }
    manifest = {
        "name": "@classmo/mobile",
        "version": "0.0.11",
        "scripts": {"typecheck": "tsc --noEmit"},
        "dependencies": original_dependencies,
        "devDependencies": {"@types/react": "~19.2.2"},
    }
    lock = {
        "name": "synthetic-workspace",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {"workspaces": ["apps/mobile"]},
            "apps/mobile": {
                "name": "@classmo/mobile",
                "version": "0.0.11",
                "dependencies": copy.deepcopy(original_dependencies),
                "devDependencies": {"@types/react": "~19.2.2"},
            },
            "node_modules/expo": {"version": "57.0.25"},
            "node_modules/react-native": {"version": "0.86.3"},
        },
    }
    root_manifest = {"private": True, "workspaces": ["apps/mobile"]}
    _write_json(repo / "package.json", root_manifest)
    _write_json(repo / "apps/mobile/package.json", manifest)
    _write_json(repo / "package-lock.json", lock)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "synthetic signed baseline")
    base = _git(repo, "rev-parse", "HEAD")

    updated_manifest = copy.deepcopy(manifest)
    updated_lock = copy.deepcopy(lock)
    updated_manifest["dependencies"].update(NEW_JS_DEPENDENCIES)
    updated_lock["packages"]["apps/mobile"]["dependencies"].update(
        NEW_JS_DEPENDENCIES
    )
    updated_lock["packages"].update(_approved_lock_additions())
    if mutation == "extra_script":
        updated_manifest["scripts"]["postinstall"] = "echo unwanted"
    elif mutation == "native_dependency":
        updated_manifest["dependencies"]["react-native-camera"] = "1.0.0"
        updated_lock["packages"]["apps/mobile"]["dependencies"][
            "react-native-camera"
        ] = "1.0.0"
    elif mutation == "expo_change":
        updated_manifest["dependencies"]["expo"] = "~58.0.0"
        updated_lock["packages"]["apps/mobile"]["dependencies"]["expo"] = "~58.0.0"
    elif mutation == "wrong_form_version":
        updated_manifest["dependencies"]["zod"] = "4.6.4"
        updated_lock["packages"]["apps/mobile"]["dependencies"]["zod"] = "4.6.4"
    elif mutation == "tampered_integrity":
        updated_lock["packages"]["apps/mobile/node_modules/zod"][
            "integrity"
        ] = "sha512-fake"
    elif mutation == "native_script_in_lock":
        updated_lock["packages"]["node_modules/react-hook-form"][
            "hasInstallScript"
        ] = True
    elif mutation == "extra_transitive_package":
        updated_lock["packages"]["node_modules/react-native-new"] = {
            "version": "1.0.0"
        }
    elif mutation == "modify_existing_package":
        updated_lock["packages"]["node_modules/expo"]["version"] = "58.0.0"
    elif mutation == "missing_lock_addition":
        updated_lock = copy.deepcopy(lock)
    elif mutation == "missing_transitive":
        del updated_lock["packages"]["node_modules/@standard-schema/utils"]
    elif mutation == "native_peer":
        updated_lock["packages"]["node_modules/react-hook-form"][
            "peerDependencies"
        ] = {"react-native": ">=0.80"}
    elif mutation == "root_manifest_changed":
        root_manifest["scripts"] = {"postinstall": "node native.js"}
    elif mutation == "native_config_changed":
        _write_json(repo / "apps/mobile/app.json", {"expo": {"plugins": ["native"]}})
    elif mutation == "native_path_added":
        native = repo / "apps/mobile/ios/Podfile"
        native.parent.mkdir(parents=True, exist_ok=True)
        native.write_text("platform :ios\n", encoding="utf-8")
    elif mutation == "root_lock_only":
        updated_manifest = copy.deepcopy(manifest)
    elif mutation == "source_only":
        updated_manifest = copy.deepcopy(manifest)
        updated_lock = copy.deepcopy(lock)
        source = repo / "apps/mobile/src/Example.tsx"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("export const value = 'safe';\n", encoding="utf-8")
    elif mutation != "valid":
        raise AssertionError(f"unknown test mutation: {mutation}")

    _write_json(repo / "package.json", root_manifest)
    _write_json(repo / "apps/mobile/package.json", updated_manifest)
    _write_json(repo / "package-lock.json", updated_lock)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"synthetic delta {mutation}")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, base, head


def _check(repo: Path, base: str, head: str, project: str = "CLASSMO") -> None:
    MODULE.check_ota(
        Namespace(
            repo=str(repo),
            project=project,
            base=base,
            head=head,
            working_directory="apps/mobile",
        )
    )


def test_019_only_approved_public_js_packages_and_versions() -> None:
    assert MODULE.CLASSMO_019_JS_DEPENDENCIES == NEW_JS_DEPENDENCIES
    assert set(MODULE.CLASSMO_019_JS_LOCK_PACKAGES) == {
        "apps/mobile/node_modules/@hookform/resolvers",
        "apps/mobile/node_modules/zod",
        "node_modules/@standard-schema/utils",
        "node_modules/react-hook-form",
    }
    for version, resolved, integrity in MODULE.CLASSMO_019_JS_LOCK_PACKAGES.values():
        assert version
        assert resolved.startswith("https://registry.npmjs.org/")
        assert integrity.startswith("sha512-")


def test_019_exact_additive_js_only_manifest_and_lock_pass(tmp_path: Path) -> None:
    _check(*_repository(tmp_path))


def test_js_source_only_without_manifest_change_still_passes(tmp_path: Path) -> None:
    _check(*_repository(tmp_path, "source_only"))


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_script",
        "native_dependency",
        "expo_change",
        "wrong_form_version",
        "tampered_integrity",
        "native_script_in_lock",
        "extra_transitive_package",
        "modify_existing_package",
        "missing_lock_addition",
        "missing_transitive",
        "native_peer",
        "root_manifest_changed",
        "native_config_changed",
        "native_path_added",
        "root_lock_only",
    ],
)
def test_ota_rejects_other_manifest_lock_or_native_changes(
    tmp_path: Path, mutation: str
) -> None:
    with pytest.raises(SystemExit, match="OTA compatibility gate blocked"):
        _check(*_repository(tmp_path, mutation))


@pytest.mark.parametrize("other_project", ["WAFL", "ESC", "MUVEL"])
def test_019_exception_does_not_apply_to_other_projects(
    tmp_path: Path, other_project: str
) -> None:
    with pytest.raises(SystemExit, match="OTA compatibility gate blocked"):
        _check(*_repository(tmp_path), project=other_project)
