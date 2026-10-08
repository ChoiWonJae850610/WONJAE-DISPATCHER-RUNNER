"""Local-only Direct Worker heartbeat and optional signed status relay.

supervise is invoked by the trusted Direct Worker workflow; watch is an OPTIONAL
separately-installed Ubuntu loop. Neither subcommand can dispatch Github jobs or
change their outcomes. No secrets enter the supervised process.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PROJECT_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")
DEFAULT_ROOT = "/var/tmp/wonjae-monitor"
HEARTBEAT_INTERVAL = 1.0
SCAN_INTERVAL = 2.0
REPORT_INTERVAL = 30.0
STALE_SECONDS = 15
HOST = "home-linux"
MAX_JOBS = 16
BRIDGE_SOCKET = "/run/wonjae-monitor-relay/relay.sock"
MAX_BRIDGE_FRAME = 4096


def private_directory(directory: Path) -> None:
    if directory.is_symlink():
        raise ValueError("monitor root cannot be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("monitor directory must be ordinary directory")
    # Fail closed if a pre-existing directory is readable by other users.
    if stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise PermissionError("monitor directory is not private (0700)")


def atomic_write(path: Path, data: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".tmp-", delete=False) as handle:
        temp = Path(handle.name)
        handle.write(data)
    try:
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def proc_start_ticks(pid: int) -> str | None:
    """Returns Linux /proc starttime + state, preventing PID-reuse false positives."""
    if pid <= 0:
        return None
    try:
        tail = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        if tail[0] in ("Z", "X"):
            return None
        return tail[19]  # field 22, indexed from field 3
    except (OSError, IndexError, ValueError):
        return None


def same_process(pid: object, start: object) -> bool:
    return isinstance(pid, int) and isinstance(start, str) and proc_start_ticks(pid) == start


def safe_json(path: Path) -> dict | None:
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def supervise(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ValueError("supervise requires a command")
    if not PROJECT_PATTERN.fullmatch(args.project) or not SHA_PATTERN.fullmatch(args.source_sha):
        raise ValueError("bad trusted project or SHA")
    if args.run_id <= 0 or args.run_attempt <= 0:
        raise ValueError("bad trusted run identity")
    os.umask(0o077)
    root = Path(args.root)
    job = root / f"{args.run_id}-{args.run_attempt}"
    enabled = True
    try:
        private_directory(root)
        if job.exists() or job.is_symlink():
            raise FileExistsError("job monitor identity already exists")
        private_directory(job)
    except (OSError, ValueError) as exc:
        # Monitor failure cannot silently grant a different execution route or
        # change an approved source task. Preserve source command exit code.
        print(f"::warning::Local heartbeat disabled: {type(exc).__name__}", flush=True)
        enabled = False

    child = subprocess.Popen(command)
    if not enabled:
        return child.wait()

    meta = {
        "project": args.project, "runId": args.run_id, "runAttempt": args.run_attempt,
        "sourceSha": args.source_sha, "phase": "CODEX",
        "supervisorPid": os.getpid(), "supervisorStart": proc_start_ticks(os.getpid()),
        "childPid": child.pid, "childStart": proc_start_ticks(child.pid),
        "startedAt": int(time.time()),
    }
    atomic_write(job / "meta.json", json.dumps(meta, sort_keys=True))
    atomic_write(job / "phase.json", '{"phase":"CODEX"}')
    stop = threading.Event()

    def beat() -> None:
        while not stop.is_set():
            try:
                atomic_write(job / "heartbeat", str(int(time.time())) + "\n")
            except OSError:
                # Do not alter source task outcome when observability fails.
                pass
            stop.wait(HEARTBEAT_INTERVAL)

    worker = threading.Thread(target=beat, name="local-heartbeat", daemon=True)
    worker.start()
    def forward(sig: int, _frame: object) -> None:
        if child.poll() is None:
            child.send_signal(sig)
    previous_term = signal.signal(signal.SIGTERM, forward)
    previous_int = signal.signal(signal.SIGINT, forward)
    try:
        exit_code = child.wait()
    finally:
        stop.set()
        worker.join(timeout=2.0)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
    try:
        atomic_write(job / "result.json", json.dumps({
            "state": "completed" if exit_code == 0 else "error",
            "exitCode": exit_code, "completedAt": int(time.time()),
        }))
    except OSError:
        pass
    return exit_code


def inspect_job(folder: Path, now: int) -> dict | None:
    meta = safe_json(folder / "meta.json")
    if not meta:
        return None
    project, source = meta.get("project"), meta.get("sourceSha")
    run_id, attempt = meta.get("runId"), meta.get("runAttempt")
    if (not isinstance(project, str) or not PROJECT_PATTERN.fullmatch(project)
            or not isinstance(source, str) or not SHA_PATTERN.fullmatch(source)
            or type(run_id) is not int or run_id < 1
            or type(attempt) is not int or attempt < 1
            or folder.name != f"{run_id}-{attempt}"):
        return None
    result = safe_json(folder / "result.json")
    if result and result.get("state") in ("completed", "error"):
        finished = result.get("completedAt", 0)
        if not isinstance(finished, int) or now - finished > 120:
            return None
        status = result["state"]
    else:
        try:
            hbpath = folder / "heartbeat"
            if hbpath.is_symlink():
                return None
            heartbeat = int(hbpath.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            heartbeat = 0
        alive = same_process(meta.get("supervisorPid"), meta.get("supervisorStart"))
        child_alive = same_process(meta.get("childPid"), meta.get("childStart"))
        status = ("running" if alive and child_alive
                  and 0 <= now - heartbeat <= STALE_SECONDS else "abnormal")
        if now - int(meta.get("startedAt") or now) > 3600 and status == "abnormal":
            return None
    return {
        "project": project, "runId": run_id, "runAttempt": attempt,
        "sourceSha": source, "phase": "CODEX", "state": status,
    }


def snapshot(root: Path, now: int) -> list[dict]:
    if not root.exists() or root.is_symlink():
        return []
    jobs = []
    for folder in root.iterdir():
        if (folder.is_symlink() or not folder.is_dir()
                or not re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*", folder.name)):
            continue
        value = inspect_job(folder, now)
        if value:
            jobs.append(value)
    # One Linux Direct Worker at a time; protect the receiver from stray old dirs.
    jobs.sort(key=lambda item: (item["runId"], item["runAttempt"]), reverse=True)
    return jobs[:MAX_JOBS]


def signed_body(jobs: list[dict], secret: str, now: int) -> tuple[bytes, str]:
    body = json.dumps({
        "version": 1, "host": HOST, "sentAt": now,
        "nonce": uuid.uuid4().hex, "jobs": jobs,
    }, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return body, "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def transmit(endpoint: str, secret: str, jobs: list[dict], now: int) -> bool:
    parsed = urllib.parse.urlparse(endpoint)
    if (parsed.scheme != "https" or parsed.path != "/api/monitor/report"
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("monitor endpoint must be HTTPS /api/monitor/report")
    body, signature = signed_body(jobs, secret, now)
    request = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Wonjae-Monitor-Signature": signature},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status == 202
    except (OSError, ValueError):
        return False


def publish_bridge_snapshot(jobs: list[dict], socket_path: str = BRIDGE_SOCKET) -> None:
    """Send status-only data through a filesystem Unix socket, never IP."""
    if not Path(socket_path).is_absolute() or len(os.fsencode(socket_path)) > 100:
        raise ValueError("invalid local bridge socket")
    frame = json.dumps({"version": 1, "host": HOST, "jobs": jobs},
                       sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(frame) > MAX_BRIDGE_FRAME:
        raise ValueError("bridge frame exceeds fixed maximum")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(2)
        channel.connect(socket_path)
        channel.sendall(frame)


def watch(args: argparse.Namespace) -> int:
    os.umask(0o077)
    root = Path(args.root)
    private_directory(root)
    endpoint = os.environ.get("WONJAE_MONITOR_ENDPOINT", "").strip()
    secret = os.environ.get("WONJAE_MONITOR_SECRET", "").strip()
    relay = bool(endpoint and len(secret) >= 32)
    bridge_socket = os.environ.get("WONJAE_MONITOR_BRIDGE_SOCKET", "").strip()
    if bridge_socket and (bridge_socket != BRIDGE_SOCKET or relay):
        print("MONITOR_BRIDGE_CONFIG_INVALID", flush=True)
        return 2
    if bridge_socket:
        print("MONITOR_LOCAL_BRIDGE_ENABLED", flush=True)
    if not relay and not bridge_socket:
        print("MONITOR_LOCAL_ONLY: relay disabled until Owner configuration", flush=True)
    previous = ""
    last_report = 0.0
    last_bridge_warning = 0.0
    while True:
        now = int(time.time())
        jobs = snapshot(root, now)
        changed = json.dumps(jobs, sort_keys=True)
        if bridge_socket:
            try:
                publish_bridge_snapshot(jobs, bridge_socket)
            except (OSError, ValueError):
                if time.monotonic() - last_bridge_warning >= REPORT_INTERVAL:
                    print("MONITOR_BRIDGE_UNAVAILABLE", flush=True)
                    last_bridge_warning = time.monotonic()
        if relay and (changed != previous or time.monotonic() - last_report >= REPORT_INTERVAL):
            try:
                if transmit(endpoint, secret, jobs, now):
                    last_report = time.monotonic()
                    previous = changed
                else:
                    # Avoid per-two-second failed remote calls.
                    last_report = time.monotonic()
                    previous = changed
                    print("MONITOR_RELAY_UNAVAILABLE", flush=True)
            except ValueError:
                print("MONITOR_ENDPOINT_INVALID", flush=True)
                return 2
        if args.once:
            print(json.dumps({"host": HOST, "jobs": jobs}, sort_keys=True), flush=True)
            break
        time.sleep(SCAN_INTERVAL)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Local-only trusted Direct Worker liveness")
    sub = parser.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("supervise")
    s.add_argument("--root", default=DEFAULT_ROOT)
    s.add_argument("--project", required=True)
    s.add_argument("--run-id", required=True, type=int)
    s.add_argument("--run-attempt", required=True, type=int)
    s.add_argument("--source-sha", required=True)
    s.add_argument("command", nargs=argparse.REMAINDER)
    w = sub.add_parser("watch")
    w.add_argument("--root", default=DEFAULT_ROOT)
    w.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.mode == "supervise":
        return supervise(args)
    return watch(args)


if __name__ == "__main__":
    sys.exit(main())
