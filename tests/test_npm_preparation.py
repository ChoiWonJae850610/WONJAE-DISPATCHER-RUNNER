from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from wonjae_dispatcher_runner.execution_state import NpmPreparation
from wonjae_dispatcher_runner.npm_preparation import (
    ARTIFACT_ROOT,
    NpmPreparationError,
    _npm_failure_reason,
    _trusted_network_environment,
    _verify_lock,
    prepare_npm,
    source_npm_environment,
)


@pytest.mark.parametrize("resolved", [
    "https://user:secret@registry.npmjs.org/pkg.tgz",
    "https://example.invalid/pkg.tgz", "file:../private", "git+https://example.invalid/repo",
    "http://registry.npmjs.org/pkg.tgz", "https://registry.npmjs.org/pkg.tgz?token=secret",
])
def test_rejects_nonpublic_dependency_sources(tmp_path, resolved):
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({"lockfileVersion": 3, "packages": {
        "node_modules/synthetic": {"resolved": resolved, "integrity": "sha512-synthetic"},
    }}))
    with pytest.raises(NpmPreparationError, match="public registry"):
        _verify_lock(lock)


def test_rejects_workspace_escape_and_missing_integrity(tmp_path):
    lock = tmp_path / "lock.json"
    for package, message in [
        ({"link": True, "resolved": "../private"}, "workspace link"),
        ({"resolved": "https://registry.npmjs.org/pkg.tgz"}, "integrity"),
    ]:
        lock.write_text(json.dumps({"lockfileVersion": 3, "packages": {"synthetic": package}}))
        with pytest.raises(NpmPreparationError, match=message):
            _verify_lock(lock)


def test_unprepared_non_npm_source_keeps_existing_environment(tmp_path):
    assert prepare_npm(tmp_path, None) is False
    assert source_npm_environment(tmp_path) == {}
    with pytest.raises(NpmPreparationError, match="no package lock"):
        prepare_npm(tmp_path, NpmPreparation("apps/client", ("synthetic@1.0.0",)))


def test_prepared_toolchain_cannot_escape_checkout(tmp_path):
    artifact = tmp_path / ARTIFACT_ROOT
    artifact.mkdir(parents=True)
    (artifact / "ready.json").write_text("{}")
    (artifact / "user.npmrc").symlink_to("/dev/null")
    with pytest.raises(NpmPreparationError, match="regular tracked"):
        source_npm_environment(tmp_path)


def test_trusted_proxy_cannot_carry_credentials(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://user:synthetic-secret@example.invalid:123")
    with pytest.raises(NpmPreparationError, match="no credentials"):
        _trusted_network_environment()


@pytest.mark.skipif(os.environ.get("RUN_NPM_PREPARATION_TESTS") != "1",
                    reason="opt-in public-registry Node 24 integration test")
def test_real_install_and_offline_source_install_preserve_original_manifests(tmp_path, monkeypatch):
    repo = tmp_path / "product"
    repo.mkdir()
    manifests = {
        "package.json": {"name": "synthetic-root", "version": "1.0.0", "private": True,
                         "workspaces": ["apps/*"],
                         "scripts": {"postinstall": "node -e 'process.exit(99)'"}},
        "apps/client/package.json": {"name": "synthetic-client", "version": "1.0.0",
                                     "private": True},
        "package-lock.json": {"name": "synthetic-root", "version": "1.0.0",
                              "lockfileVersion": 3, "requires": True, "packages": {
                                  "": {"name": "synthetic-root", "version": "1.0.0",
                                         "workspaces": ["apps/*"], "hasInstallScript": True},
                                  "apps/client": {"name": "synthetic-client", "version": "1.0.0"},
                                  "node_modules/synthetic-client": {
                                      "resolved": "apps/client", "link": True},
                              }},
    }
    for relative, content in manifests.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(content))
    (repo / ".gitignore").write_text("node_modules/\n")
    for args in [("init",), ("add", ".")]:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    original = {relative: (repo / relative).read_bytes() for relative in manifests}
    calls = []
    actual_run = subprocess.run

    def observed_run(args, **kwargs):
        if "npm-cli.js" in " ".join(str(arg) for arg in args):
            calls.append((args, kwargs["env"]))
        return actual_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", observed_run)
    monkeypatch.setenv("PRODUCT_TOKEN", "synthetic-secret")
    monkeypatch.setenv("NPM_TOKEN", "synthetic-secret")
    assert prepare_npm(repo, NpmPreparation("apps/client", ("picocolors@1.1.1",)))
    # Ignore optional-platform tarballs: only actual offline reinstallation
    # proves the locked dependencies needed by this source host are complete.
    assert len(calls) == 4
    assert calls[2][0][2:4] == ["cache", "verify"]
    assert not any(command[0][2:4] == ["cache", "add"] for command in calls)
    assert calls[-1][0][2:4] == ["ci", "--offline"]
    assert "--offline" in calls[-1][0]
    for args, env in calls:
        assert "--ignore-scripts" in args
        assert "synthetic-secret" not in json.dumps(env)
        assert set(env) <= {"PATH", "HOME", "LANG", "TZ",
                            "NPM_CONFIG_PROXY", "NPM_CONFIG_HTTPS_PROXY", "NODE_EXTRA_CA_CERTS"}
    assert all((repo / path).read_bytes() == data for path, data in original.items())
    assert subprocess.run(["git", "-C", str(repo), "diff", "--exit-code"],
                          capture_output=True).returncode == 0
    safe = source_npm_environment(repo)
    env = {"PATH": str(repo / ARTIFACT_ROOT / "tools/bin") + ":/usr/bin:/bin",
           "HOME": "/nonexistent", "LANG": "C.UTF-8", "TZ": "UTC", **safe}
    npm = shutil.which("npm", path=env["PATH"])
    subprocess.run([npm, "install", "picocolors@1.1.1", "--workspace", "apps/client",
                    "--save-exact", "--offline", "--ignore-scripts"], cwd=repo,
                   env=env, check=True, capture_output=True, timeout=60)
    manifest = json.loads((repo / "apps/client/package.json").read_text())
    lock = json.loads((repo / "package-lock.json").read_text())
    assert manifest["dependencies"]["picocolors"] == "1.1.1"
    assert lock["packages"]["node_modules/picocolors"]["version"] == "1.1.1"
    assert lock["packages"]["node_modules/picocolors"]["integrity"].startswith("sha512-")


@pytest.mark.parametrize(("stderr", "expected"), [
    ("npm error No workspaces found:\nnpm error --workspace=apps/mobile", "NO_WORKSPACES"),
    ("npm ERR! code ERESOLVE\nnpm ERR! untrusted material", "ERESOLVE"),
    ("npm error code ENOTCACHED\nnpm error token=private", "ENOTCACHED"),
    ("npm error code UNEXPECTED_PRIVATE_DATA", "UNCLASSIFIED"),
])
def test_npm_failure_reason_redacts_arbitrary_output(stderr, expected):
    assert _npm_failure_reason(stderr) == expected


def test_independent_npm_project_requires_own_tracked_lock(tmp_path):
    repo = tmp_path / "project"
    (repo / "apps/mobile").mkdir(parents=True)
    (repo / "package.json").write_text('{"name":"synthetic-root","version":"1.0.0"}')
    (repo / "package-lock.json").write_text(json.dumps({
        "lockfileVersion": 3, "packages": {"": {"name": "synthetic-root", "version": "1.0.0"}},
    }))
    (repo / "apps/mobile/package.json").write_text(
        '{"name":"synthetic-mobile","version":"1.0.0"}'
    )
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    with pytest.raises(NpmPreparationError, match="independent npm package requires tracked lock"):
        prepare_npm(repo, NpmPreparation("apps/mobile", ("picocolors@1.1.1",)))


@pytest.mark.skipif(os.environ.get("RUN_NPM_PREPARATION_TESTS") != "1",
                    reason="opt-in public-registry Node 24 integration test")
def test_independent_mobile_lock_prefix_install_and_offline_replay(tmp_path, monkeypatch):
    repo = tmp_path / "product"
    repo.mkdir()
    manifests = {
        "package.json": {"name": "synthetic-root", "version": "1.0.0", "private": True},
        "package-lock.json": {"name": "synthetic-root", "version": "1.0.0",
                              "lockfileVersion": 3, "requires": True, "packages": {
                                  "": {"name": "synthetic-root", "version": "1.0.0"}}},
        "apps/mobile/package.json": {
            "name": "synthetic-mobile", "version": "1.0.0", "private": True,
            "scripts": {"postinstall": "node -e 'process.exit(99)'"},
        },
        "apps/mobile/package-lock.json": {
            "name": "synthetic-mobile", "version": "1.0.0",
            "lockfileVersion": 3, "requires": True, "packages": {
                "": {"name": "synthetic-mobile", "version": "1.0.0",
                     "hasInstallScript": True},
            },
        },
    }
    for relative, content in manifests.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content))
    (repo / ".gitignore").write_text("node_modules/\n")
    for command in [("init",), ("add", ".")]:
        subprocess.run(["git", "-C", str(repo), *command], check=True, capture_output=True)
    original = {path: (repo / path).read_bytes() for path in manifests}
    calls = []
    real_run = subprocess.run

    def record_run(args, **kwargs):
        if any("npm-cli.js" in str(arg) for arg in args):
            calls.append((args, kwargs["env"]))
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", record_run)
    monkeypatch.setenv("NPM_TOKEN", "synthetic-secret")
    assert prepare_npm(repo, NpmPreparation("apps/mobile", ("picocolors@1.1.1",)))
    assert len(calls) == 6
    assert calls[1][0][2:5] == ["ci", "--prefix", "apps/mobile"]
    assert calls[2][0][2:7] == [
        "install", "picocolors@1.1.1", "--prefix", "apps/mobile", "--save-exact",
    ]
    assert calls[3][0][2:4] == ["cache", "verify"]
    assert calls[4][0][2:4] == ["ci", "--offline"]
    assert calls[5][0][2:6] == ["ci", "--prefix", "apps/mobile", "--offline"]
    assert all("--ignore-scripts" in args for args, _ in calls)
    assert all("synthetic-secret" not in json.dumps(env) for _, env in calls)
    assert (repo / "apps/mobile/node_modules/picocolors/package.json").is_file()
    assert all((repo / path).read_bytes() == content for path, content in original.items())

    safe = source_npm_environment(repo)
    env = {"PATH": str(repo / ARTIFACT_ROOT / "tools/bin") + ":/usr/bin:/bin",
           "HOME": "/nonexistent", "LANG": "C.UTF-8", "TZ": "UTC", **safe}
    binary = shutil.which("npm", path=env["PATH"])
    real_run(
        [binary, "install", "picocolors@1.1.1", "--prefix", "apps/mobile",
         "--save-exact", "--offline", "--ignore-scripts"],
        cwd=repo, env=env, check=True, capture_output=True, timeout=60,
    )
    mobile = json.loads((repo / "apps/mobile/package.json").read_text())
    mobile_lock = json.loads((repo / "apps/mobile/package-lock.json").read_text())
    assert mobile["dependencies"]["picocolors"] == "1.1.1"
    assert mobile_lock["packages"]["node_modules/picocolors"]["version"] == "1.1.1"
    assert mobile_lock["packages"]["node_modules/picocolors"]["integrity"].startswith("sha512-")
