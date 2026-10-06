"""Restricted-read workspace-write profile for persistent Linux source workers.

The SDK's legacy workspace-write turn override drops split read restrictions.
Use its :workspace baseline through a named profile and retain that profile on
both thread and turn. This module grants no Git/network/provider authority.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from codex_cli_bin import bundled_codex_path

PROFILE = "direct_source"
HOME_LABELS = ("self-hosted", "Linux", "X64", "direct-worker")
PROTECTED_DIRECTORIES = (".git", ".github", ".wonjae", ".codex", ".agents")
PROTECTED_FILES = ("AGENTS.md", "PROJECT_RULES.md", ".gitmodules")


@contextmanager
def protected_mount_placeholders(repo: Path):
    """Own missing mount targets; remove only unchanged empty trusted placeholders.

    The pinned Linux helper otherwise creates missing read-only mount targets on
    the host checkout. Never ignore those paths in Git or permit model writes.
    """
    owned = []
    try:
        for name in (*PROTECTED_DIRECTORIES, *PROTECTED_FILES):
            target = repo / name
            if target.is_symlink():
                raise ValueError("protected mount target must not be a symlink")
            if target.exists():
                continue
            if name in PROTECTED_DIRECTORIES:
                target.mkdir(mode=0o700)
            else:
                with target.open("xb"):
                    pass
                target.chmod(0o600)
            stat = target.stat()
            owned.append((target, stat.st_dev, stat.st_ino))
        yield
    finally:
        for target, device, inode in reversed(owned):
            stat = target.lstat()
            if target.is_symlink() or (stat.st_dev, stat.st_ino) != (device, inode):
                raise ValueError("trusted mount placeholder identity changed")
            if target.is_dir():
                target.rmdir()  # Nonempty means a real boundary failure; never recurse.
            elif target.is_file() and stat.st_size == 0:
                target.unlink()
            else:
                raise ValueError("trusted mount placeholder was modified")


def source_environment(codex_home: Path, shell_home: Path) -> dict[str, str]:
    # Fixed Linux runtime paths; never inherit host/user PATH, HOME, proxy or tokens.
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(shell_home),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "CODEX_HOME": str(codex_home),
        "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
    }


def permission_config(repo: Path) -> dict:
    root = repo.resolve(strict=True)
    if repo.is_symlink() or not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise ValueError("home worker requires an independent non-symlink checkout")
    if str(root).startswith(("/mnt/", "/media/")):
        raise ValueError("home worker checkout must be on the Linux filesystem")
    filesystem = {
        ":root": "deny", ":minimal": "read", ":tmpdir": "deny", ":slash_tmp": "deny",
        str(root): "write",
    }
    # Only the pinned public runtime binaries/resources, never the whole venv/cache.
    filesystem[str(bundled_codex_path().parent.resolve())] = "read"
    # Product instructions and integration authority are readable, never model-writable.
    for relative in (*PROTECTED_DIRECTORIES, *PROTECTED_FILES):
        if (root / relative).is_symlink():
            raise ValueError("protected mount target must not be a symlink")
        filesystem[str(root / relative)] = "read"
    return {
        "default_permissions": PROFILE,
        "permissions": {
            PROFILE: {
                "extends": ":workspace",
                "workspace_roots": {str(root): True},
                "filesystem": filesystem,
                "network": {"enabled": False},
            },
        },
        "features": {"use_legacy_landlock": False},
        "web_search": "disabled",
    }


def cli_overrides(repo: Path) -> tuple[str, ...]:
    def toml(value):
        if isinstance(value, dict):
            return "{" + ",".join(f"{json.dumps(k)}={toml(v)}" for k, v in value.items()) + "}"
        return json.dumps(value)

    return tuple(f"{key}={toml(value)}" for key, value in permission_config(repo).items())


def require_linux_host() -> None:
    if os.name != "posix" or not Path("/proc/sys/kernel/osrelease").is_file():
        raise ValueError("home Direct Worker requires Linux/WSL2, never Windows-native")
    release = Path("/proc/sys/kernel/osrelease").read_text().lower()
    if "microsoft" in release and "wsl2" not in release:
        raise ValueError("WSL1 cannot enforce the Linux isolation contract")
