"""Opt-in remote second Linux runner setup through a trusted Owner workflow.

No command in this module is executed by import, a PR, or ordinary Direct Worker.
The host-side workflow invokes it only after explicit Owner workflow_dispatch.
All failure messages are fixed non-secret codes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import pwd
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from wonjae_dispatcher_runner.credential_slot import require_independent_auth

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_LABELS = frozenset({"self-hosted", "Linux", "X64", "direct-worker"})
RUNNER_SUBDIR = "wonjae-direct-worker-secondary"
MIN_AVAILABLE_MEMORY = 6 * (1024**3)
MIN_AVAILABLE_DISK = 12 * (1024**3)


class SetupBlocked(RuntimeError):
    """Bounded, sanitized fail-closed setup result."""


def check_names(primary: str, secondary: str, actual: str, operation: str) -> None:
    if (not NAME_RE.fullmatch(primary) or not NAME_RE.fullmatch(secondary)
            or primary == secondary):
        raise SetupBlocked("SECOND_RUNNER_NAME_INVALID")
    if operation not in {"inspect", "activate", "verify"}:
        raise SetupBlocked("SECOND_RUNNER_OPERATION_INVALID")
    if operation in {"inspect", "activate"} and actual != primary:
        raise SetupBlocked("SECOND_RUNNER_PRIMARY_HOST_MISMATCH")
    if operation == "verify" and actual not in {primary, secondary}:
        raise SetupBlocked("SECOND_RUNNER_HOST_MISMATCH")


def second_root(home: Path) -> Path:
    if not home.is_absolute() or not home.is_dir() or home.is_symlink():
        raise SetupBlocked("SECOND_RUNNER_HOME_INVALID")
    root = home / RUNNER_SUBDIR
    if root.exists() or root.is_symlink():
        raise SetupBlocked("SECOND_RUNNER_ROOT_ALREADY_EXISTS")
    return root


def host_facts(home: Path) -> dict[str, object]:
    mem_info = Path("/proc/meminfo").read_text(encoding="ascii")
    found = re.search(r"^MemAvailable:\s+(\d+)\s+kB", mem_info, re.MULTILINE)
    if not found:
        raise SetupBlocked("SECOND_RUNNER_MEMORY_UNVERIFIED")
    available = int(found.group(1)) * 1024
    disk = shutil.disk_usage(home).free
    try:
        pid1 = Path("/proc/1/comm").read_text(encoding="ascii").strip()
    except OSError:
        pid1 = "unknown"
    kernel = platform.release().lower()
    try:
        sudo = subprocess.run(
            ["sudo", "-n", "true"], check=False, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        sudo = False
    return {
        "linux": platform.system() == "Linux",
        "wsl2": "microsoft" in kernel and "wsl2" in kernel,
        "systemd": pid1 == "systemd",
        "sudo_noninteractive": sudo,
        "memory_gib": available // (1024**3),
        "disk_gib": disk // (1024**3),
        "capacity_ok": available >= MIN_AVAILABLE_MEMORY and disk >= MIN_AVAILABLE_DISK,
    }


def require_activation_host(facts: dict[str, object]) -> None:
    for key in ("linux", "wsl2", "systemd", "sudo_noninteractive", "capacity_ok"):
        if facts.get(key) is not True:
            raise SetupBlocked("SECOND_RUNNER_HOST_BLOCKED_" + key.upper())


def safe_command(args: list[str], *, cwd: Path | None = None, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            args, cwd=cwd, check=False, capture_output=True, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupBlocked("SECOND_RUNNER_COMMAND_FAILED") from exc
    if result.returncode:
        # Never surface stdout, stderr, command arguments, or a token-bearing payload.
        raise SetupBlocked("SECOND_RUNNER_COMMAND_FAILED")
    return result.stdout


def gh_api(endpoint: str, method: str = "GET") -> dict:
    if not os.environ.get("GH_TOKEN"):
        raise SetupBlocked("SECOND_RUNNER_GITHUB_AUTH_MISSING")
    args = ["gh", "api", "--method", method, endpoint]
    try:
        result = json.loads(safe_command(args))
    except ValueError as exc:
        raise SetupBlocked("SECOND_RUNNER_GITHUB_RESPONSE_INVALID") from exc
    if not isinstance(result, dict):
        raise SetupBlocked("SECOND_RUNNER_GITHUB_RESPONSE_INVALID")
    return result


def runner_inventory(payload: dict, primary: str, secondary: str,
                     *, require_secondary: bool) -> None:
    runners = payload.get("runners")
    count = payload.get("total_count")
    if (not isinstance(runners, list) or type(count) is not int
            or count > len(runners) or len(runners) > 100):
        raise SetupBlocked("SECOND_RUNNER_INVENTORY_INCOMPLETE")
    by_name = {
        r.get("name"): r
        for r in runners if isinstance(r, dict) and isinstance(r.get("name"), str)
    }
    if len(by_name) != len(runners):
        raise SetupBlocked("SECOND_RUNNER_INVENTORY_AMBIGUOUS")
    for name in (primary, secondary) if require_secondary else (primary,):
        item = by_name.get(name)
        if not item or item.get("status") != "online":
            raise SetupBlocked("SECOND_RUNNER_REQUIRED_HOST_NOT_ONLINE")
        labels = item.get("labels")
        if not isinstance(labels, list):
            raise SetupBlocked("SECOND_RUNNER_LABELS_UNVERIFIED")
        actual = {lab.get("name") for lab in labels if isinstance(lab, dict)}
        if not EXPECTED_LABELS.issubset(actual):
            raise SetupBlocked("SECOND_RUNNER_LABELS_MISMATCH")
    if not require_secondary and secondary in by_name:
        raise SetupBlocked("SECOND_RUNNER_ALREADY_REGISTERED")


def git_variables(repository: str) -> dict[str, str]:
    raw = safe_command([
        "gh", "variable", "list", "--repo", repository, "--json", "name,value",
    ])
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise SetupBlocked("SECOND_RUNNER_VARIABLES_INVALID") from exc
    if not isinstance(parsed, list):
        raise SetupBlocked("SECOND_RUNNER_VARIABLES_INVALID")
    return {
        x["name"]: x["value"] for x in parsed
        if isinstance(x, dict) and isinstance(x.get("name"), str)
        and isinstance(x.get("value"), str)
    }


def require_idle_direct_worker(repository: str) -> None:
    for status in ("queued", "in_progress", "requested", "waiting", "pending"):
        inventory = gh_api(
            f"repos/{repository}/actions/workflows/direct-worker.yml/runs"
            f"?status={status}&per_page=100"
        )
        count = inventory.get("total_count")
        if type(count) is not int:
            raise SetupBlocked("SECOND_RUNNER_ACTIVE_RUNS_UNVERIFIED")
        if count:
            raise SetupBlocked("SECOND_RUNNER_ACTIVE_DIRECT_WORKER")


def select_runner_asset(release: dict) -> tuple[str, str]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise SetupBlocked("SECOND_RUNNER_RELEASE_UNVERIFIED")
    matching = [
        item for item in assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
        and re.fullmatch(r"actions-runner-linux-x64-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz",
                         item["name"])
    ]
    if len(matching) != 1:
        raise SetupBlocked("SECOND_RUNNER_RELEASE_AMBIGUOUS")
    item = matching[0]
    url = item.get("browser_download_url")
    digest = item.get("digest")
    if not isinstance(url, str) or not isinstance(digest, str):
        raise SetupBlocked("SECOND_RUNNER_RELEASE_DIGEST_MISSING")
    host = urlparse(url)
    if (host.scheme != "https" or host.hostname != "github.com"
            or not host.path.startswith("/actions/runner/releases/download/")):
        raise SetupBlocked("SECOND_RUNNER_RELEASE_URL_UNTRUSTED")
    if not digest.startswith("sha256:") or not SHA_RE.fullmatch(digest[7:]):
        raise SetupBlocked("SECOND_RUNNER_RELEASE_DIGEST_INVALID")
    return url, digest[7:]


def download_verified_archive(url: str, digest: str, temporary: Path) -> Path:
    target = temporary / "runner.tar.gz"
    safe_command(
        ["curl", "-fL", "--proto", "=https", "--tlsv1.2", "--retry", "2",
         "--connect-timeout", "10", "--max-time", "240", "--output", str(target), url],
        timeout=260,
    )
    hasher = hashlib.sha256()
    with target.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    if hasher.hexdigest() != digest:
        raise SetupBlocked("SECOND_RUNNER_ARCHIVE_DIGEST_MISMATCH")
    return target


def set_slot_variables(repository: str, primary: str, secondary: str) -> None:
    expected = {
        "DW_PRIMARY_RUNNER_NAME": primary,
        "DW_SECONDARY_RUNNER_NAME": secondary,
    }
    prior = git_variables(repository)
    for name, value in expected.items():
        if prior.get(name) not in (None, value):
            raise SetupBlocked("SECOND_RUNNER_VARIABLE_CONFLICT")
    created: list[str] = []
    try:
        for name, value in expected.items():
            if name not in prior:
                safe_command(
                    ["gh", "variable", "set", name, "--repo", repository, "--body", value]
                )
                created.append(name)
        observed = git_variables(repository)
        if any(observed.get(name) != value for name, value in expected.items()):
            raise SetupBlocked("SECOND_RUNNER_VARIABLE_READBACK_MISMATCH")
    except SetupBlocked:
        # Roll back ONLY names newly created by this invocation, never existing variables.
        for name in reversed(created):
            try:
                safe_command(["gh", "variable", "delete", name, "--repo", repository])
            except SetupBlocked:
                raise SetupBlocked("SECOND_RUNNER_VARIABLE_ROLLBACK_MANUAL_REQUIRED") from None
        raise


def verify_second_runner(repository: str, primary: str, secondary: str) -> None:
    variables = git_variables(repository)
    if (variables.get("DW_PRIMARY_RUNNER_NAME") != primary
            or variables.get("DW_SECONDARY_RUNNER_NAME") != secondary):
        raise SetupBlocked("SECOND_RUNNER_VARIABLES_NOT_READY")
    inventory = gh_api(f"repos/{repository}/actions/runners?per_page=100")
    runner_inventory(inventory, primary, secondary, require_secondary=True)


def activate(repository: str, primary: str, secondary: str) -> None:
    if os.environ.get("GITHUB_ACTOR") != os.environ.get("GITHUB_REPOSITORY_OWNER"):
        raise SetupBlocked("SECOND_RUNNER_OWNER_REQUIRED")
    home = Path.home()
    root = second_root(home)
    require_activation_host(host_facts(home))
    require_idle_direct_worker(repository)
    inventory = gh_api(f"repos/{repository}/actions/runners?per_page=100")
    runner_inventory(inventory, primary, secondary, require_secondary=False)
    require_independent_auth(
        os.environ.get("PRIMARY_CODEX_AUTH", ""),
        os.environ.get("SECONDARY_CODEX_AUTH", ""),
    )
    if not shutil.which("gh") or not shutil.which("curl") or not shutil.which("tar"):
        raise SetupBlocked("SECOND_RUNNER_TOOLS_MISSING")
    release = gh_api("repos/actions/runner/releases/latest")
    url, digest = select_runner_asset(release)

    # Prove repository Administration:write BEFORE changing variables or host files.
    response = gh_api(
        f"repos/{repository}/actions/runners/registration-token", method="POST"
    )
    registration_token = response.get("token")
    if not isinstance(registration_token, str) or not registration_token:
        raise SetupBlocked("SECOND_RUNNER_REGISTRATION_AUTH_UNVERIFIED")

    with tempfile.TemporaryDirectory(prefix="second-runner-archive-") as folder:
        archive = download_verified_archive(url, digest, Path(folder))
        root.mkdir(mode=0o700)
        safe_command(
            ["tar", "--no-same-owner", "--no-same-permissions",
             "-xzf", str(archive), "-C", str(root)],
            timeout=100,
        )
        config = root / "config.sh"
        service = root / "svc.sh"
        if not config.is_file() or not service.is_file():
            raise SetupBlocked("SECOND_RUNNER_PACKAGE_INVALID")
        set_slot_variables(repository, primary, secondary)
        try:
            # Registration token is passed only to the trusted local process,
            # never to output, shell tracing, GitHub logs or artifacts.
            safe_command(
                [str(config), "--unattended", "--url", f"https://github.com/{repository}",
                 "--token", registration_token, "--name", secondary,
                 "--labels", "direct-worker", "--work", "_work"],
                cwd=root, timeout=120,
            )
        finally:
            registration_token = ""
        user = pwd.getpwuid(os.getuid()).pw_name
        safe_command(["sudo", "-n", str(service), "install", user], cwd=root)
        safe_command(["sudo", "-n", str(service), "start"], cwd=root)
        # A short bounded readback is not a background watcher.
        for attempt in range(6):
            try:
                verify_second_runner(repository, primary, secondary)
                print("SECOND_RUNNER_ACTIVATED_AND_ONLINE")
                return
            except SetupBlocked:
                if attempt == 5:
                    raise
                time.sleep(3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", choices=("inspect", "activate", "verify"),
                        required=True)
    args = parser.parse_args()
    try:
        primary = os.environ.get("DW_PRIMARY_NAME", "")
        secondary = os.environ.get("DW_SECONDARY_NAME", "")
        actual = os.environ.get("RUNNER_NAME", "")
        check_names(primary, secondary, actual, args.operation)
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise SetupBlocked("SECOND_RUNNER_REPOSITORY_INVALID")
        if args.operation == "inspect":
            facts = host_facts(Path.home())
            root = Path.home() / RUNNER_SUBDIR
            print("SECOND_RUNNER_INSPECTION=" + json.dumps({
                **facts,
                "target_unused": not root.exists() and not root.is_symlink(),
            }, sort_keys=True))
            print("SECOND_RUNNER_INSPECT_ONLY_NO_MUTATION")
        elif args.operation == "verify":
            verify_second_runner(repository, primary, secondary)
            print("SECOND_RUNNER_VERIFY_PASS")
        else:
            activate(repository, primary, secondary)
    except SetupBlocked as exc:
        parser.exit(2, "SECOND_RUNNER_BLOCKED=" + str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
