"""Credential-free trusted npm preparation; source turns remain offline."""

from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .execution_state import NpmPreparation

ARTIFACT_ROOT = Path("node_modules/.direct-worker")


class NpmPreparationError(RuntimeError):
    pass


def _trusted_network_environment() -> dict[str, str]:
    """Allow credential-free host transport only in trusted preparation, never Codex."""
    result = {}
    for names, target in (
        (("HTTPS_PROXY", "https_proxy"), "NPM_CONFIG_HTTPS_PROXY"),
        (("HTTP_PROXY", "http_proxy"), "NPM_CONFIG_PROXY"),
    ):
        value = next((os.environ[name] for name in names if os.environ.get(name)), None)
        if value is None:
            continue
        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise NpmPreparationError("trusted npm transport proxy must contain no credentials")
        result[target] = value
    certificate = os.environ.get("NODE_EXTRA_CA_CERTS")
    if certificate:
        try:
            ssl.create_default_context(cafile=certificate)
        except (OSError, ssl.SSLError) as exc:
            raise NpmPreparationError("trusted npm CA certificate is invalid") from exc
        result["NODE_EXTRA_CA_CERTS"] = certificate
    return result


def _regular(repo: Path, relative: str) -> Path:
    path = repo / relative
    if not path.is_file() or any(part.is_symlink() for part in (path, *path.parents)):
        raise NpmPreparationError("npm preparation requires regular tracked manifests")
    if not path.resolve().is_relative_to(repo.resolve()):
        raise NpmPreparationError("npm manifest escapes product checkout")
    return path


def _verify_lock(path: Path) -> None:
    payload = json.loads(path.read_text())
    if payload.get("lockfileVersion") not in (2, 3) or not isinstance(
        payload.get("packages"), dict
    ):
        raise NpmPreparationError("npm preparation requires a modern package lock")
    for package in payload["packages"].values():
        if package.get("link"):
            relative = Path(package.get("resolved", ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise NpmPreparationError("npm workspace link escapes checkout")
            continue
        resolved = package.get("resolved")
        if resolved:
            url = urlsplit(resolved)
            if (
                url.scheme != "https"
                or url.hostname != "registry.npmjs.org"
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise NpmPreparationError("npm preparation accepts only public registry tarballs")
            if not package.get("integrity"):
                raise NpmPreparationError("npm tarball is missing integrity metadata")


def source_npm_environment(repo: Path) -> dict[str, str]:
    root = repo / ARTIFACT_ROOT
    if not (root / "ready.json").is_file():
        return {}
    for relative in (
        "ready.json",
        "user.npmrc",
        "global.npmrc",
        "tools/bin/node",
        "tools/lib/node_modules/npm/bin/npm-cli.js",
    ):
        _regular(repo, str(ARTIFACT_ROOT / relative))
    cache = root / "cache"
    if (
        not cache.is_dir()
        or cache.is_symlink()
        or not cache.resolve().is_relative_to(repo.resolve())
    ):
        raise NpmPreparationError("npm cache escapes product checkout")
    return {
        "NPM_CONFIG_CACHE": str(cache),
        "NPM_CONFIG_OFFLINE": "true",
        "NPM_CONFIG_IGNORE_SCRIPTS": "true",
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_USERCONFIG": str(root / "user.npmrc"),
        "NPM_CONFIG_GLOBALCONFIG": str(root / "global.npmrc"),
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
    }


def prepare_npm(repo: Path, preparation: NpmPreparation | None) -> bool:
    """Warm genuine dependency artifacts without changing any tracked product file."""
    if not (repo / "package-lock.json").exists():
        if preparation:
            raise NpmPreparationError("declared npm preparation has no package lock")
        return False
    tracked = (
        subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"],
            check=True,
            capture_output=True,
            timeout=20,
        )
        .stdout.decode()
        .split("\0")
    )
    manifests = [
        path for path in tracked if path == "package.json" or path.endswith("/package.json")
    ]
    files = {path: _regular(repo, path).read_bytes() for path in ["package-lock.json", *manifests]}
    if "package.json" not in files or "package-lock.json" not in tracked:
        raise NpmPreparationError("npm preparation requires tracked root manifests")
    if preparation and f"{preparation.workspace}/package.json" not in files:
        raise NpmPreparationError("declared npm workspace is not tracked")
    _verify_lock(repo / "package-lock.json")
    ignored = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--quiet", str(ARTIFACT_ROOT / "ready.json")],
        check=False,
        timeout=20,
    )
    if ignored.returncode != 0:
        raise NpmPreparationError("product must ignore node_modules preparation artifacts")
    node = shutil.which("node")
    if not node:
        raise NpmPreparationError("trusted Node 24 toolchain is unavailable")
    toolchain = Path(node).resolve().parent.parent
    npm_cli = toolchain / "lib/node_modules/npm/bin/npm-cli.js"
    if not npm_cli.is_file():
        raise NpmPreparationError("trusted standalone npm toolchain is unavailable")
    version = subprocess.run([node, "--version"], check=True, capture_output=True, text=True).stdout
    if not version.startswith("v24."):
        raise NpmPreparationError("trusted preparation requires Node 24")

    with tempfile.TemporaryDirectory(prefix="npm-prepare-", dir=repo.parent) as temporary:
        stage = Path(temporary)
        for relative, content in files.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        cache = stage / ".npm-cache"
        shell_home = stage / ".home"
        shell_home.mkdir()
        (stage / "user.npmrc").touch()
        (stage / "global.npmrc").touch()
        # Never inherit tokens, host npmrc, Git credential helpers or lifecycle scripts.
        env = {
            "PATH": str(toolchain / "bin") + ":/usr/bin:/bin",
            "HOME": str(shell_home),
            "LANG": "C.UTF-8",
            "TZ": "UTC",
        }
        env.update(_trusted_network_environment())
        options = [
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--update-notifier=false",
            "--fetch-retries=1",
            "--fetch-timeout=30000",
            "--registry=https://registry.npmjs.org",
            f"--cache={cache}",
            f"--userconfig={stage / 'user.npmrc'}",
            f"--globalconfig={stage / 'global.npmrc'}",
        ]

        def npm(*args: str) -> None:
            result = subprocess.run(
                [node, str(npm_cli), *args, *options],
                cwd=stage,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode:
                # Do not echo arbitrary product metadata or host configuration into public logs.
                raise NpmPreparationError(
                    f"trusted npm {args[0]} failed (exit {result.returncode})"
                )

        npm("ci")
        if preparation:
            npm(
                "install",
                *preparation.packages,
                "--workspace",
                preparation.workspace,
                "--save-exact",
            )
        _verify_lock(stage / "package-lock.json")
        # An online install can leave partially populated cache entries. Explicitly
        # consume every locked tarball before claiming the offline cache is complete.
        npm("cache", "verify")
        lock = json.loads((stage / "package-lock.json").read_text())
        tarballs = sorted({package["resolved"] for package in lock["packages"].values()
                           if package.get("resolved") and not package.get("link")})
        for offset in range(0, len(tarballs), 50):
            npm("cache", "add", *tarballs[offset:offset + 50], "--prefer-offline")
        npm("cache", "verify")
        # A second install must succeed with network requests forbidden by npm itself.
        npm("ci", "--offline")
        for installed in sorted(stage.rglob("node_modules"), key=lambda path: len(path.parts)):
            if any(parent.name == "node_modules" for parent in installed.parents):
                continue
            target = repo / installed.relative_to(stage)
            if target.exists() or target.is_symlink():
                raise NpmPreparationError("npm preparation requires a fresh dependency directory")
            shutil.copytree(installed, target, symlinks=True)
        artifact = repo / ARTIFACT_ROOT
        artifact.mkdir()
        (artifact / "user.npmrc").touch()
        (artifact / "global.npmrc").touch()
        shutil.copytree(cache, artifact / "cache")
        (artifact / "tools/bin").mkdir(parents=True)
        shutil.copy2(node, artifact / "tools/bin/node")
        shutil.copytree(
            toolchain / "lib/node_modules/npm",
            artifact / "tools/lib/node_modules/npm",
            symlinks=True,
        )
        (artifact / "tools/bin/npm").symlink_to("../lib/node_modules/npm/bin/npm-cli.js")
        (artifact / "tools/bin/npx").symlink_to("../lib/node_modules/npm/bin/npx-cli.js")
        (artifact / "ready.json").write_text(json.dumps({"node": version.strip(), "offline": True}))
    if any(_regular(repo, path).read_bytes() != content for path, content in files.items()):
        raise NpmPreparationError("trusted npm preparation modified a product manifest")
    source_npm_environment(repo)
    return True
