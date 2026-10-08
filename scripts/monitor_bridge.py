"""A fixed-target HTTPS egress bridge for an isolated local heartbeat watcher.

The watcher can run with systemd PrivateNetwork=true. It publishes only bounded,
non-secret process status over AF_UNIX. This separately supervised bridge checks
SO_PEERCRED, signs frames with a systemd LoadCredential, and contacts one fixed
HTTPS host/path. No arbitrary destination, redirect, HTTP proxy, or product job
execution API is exposed. Hostname policy is application-level, not a kernel
DNS-based firewall: see docs/RESTRICTED_MONITOR_BRIDGE.md.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import pwd
import re
import signal
import socket
import ssl
import stat
import struct
import sys
import time
from pathlib import Path

from local_monitor import signed_body

HOST = "sanjinworks.com"
PORT = 443
REPORT_PATH = "/api/monitor/report"
SOCKET_PATH = Path("/run/wonjae-monitor-relay/relay.sock")
MAX_FRAME = 4096
MAX_JOBS = 16
REPORT_INTERVAL = 30.0
VALID_SHA = re.compile(r"^[a-f0-9]{40}$")
VALID_PROJECT = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
FIELDS = {"project", "runId", "runAttempt", "sourceSha", "phase", "state"}
STATES = {"running", "abnormal", "error", "completed"}


def validate_frame(payload: bytes) -> list[dict]:
    """Fail closed on oversized, malformed or extra-field local status data."""
    if not payload or len(payload) > MAX_FRAME:
        raise ValueError("invalid monitor frame size")
    try:
        frame = json.loads(payload.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid monitor frame JSON") from exc
    if (not isinstance(frame, dict) or set(frame) != {"version", "host", "jobs"}
            or type(frame["version"]) is not int or frame["version"] != 1
            or frame["host"] != "home-linux" or not isinstance(frame["jobs"], list)
            or len(frame["jobs"]) > MAX_JOBS):
        raise ValueError("invalid monitor frame")
    validated: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for job in frame["jobs"]:
        if not isinstance(job, dict) or set(job) != FIELDS:
            raise ValueError("invalid monitor job fields")
        project, run_id, attempt = job["project"], job["runId"], job["runAttempt"]
        source = job["sourceSha"]
        if (not isinstance(project, str) or not VALID_PROJECT.fullmatch(project)
                or project == "KDN" or type(run_id) is not int
                or not 0 < run_id <= 9007199254740991 or type(attempt) is not int
                or not 0 < attempt <= 9007199254740991
                or not isinstance(source, str) or not VALID_SHA.fullmatch(source)
                or job["phase"] != "CODEX" or not isinstance(job["state"], str)
                or job["state"] not in STATES):
            raise ValueError("invalid monitor job identity")
        identity = (project, run_id, attempt)
        if identity in seen:
            raise ValueError("duplicate monitor job")
        seen.add(identity)
        validated.append(dict(job))
    return validated


def read_peer_frame(connection: socket.socket, allowed_uid: int) -> list[dict]:
    """Authenticate the Linux client UID before accepting a bounded frame."""
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("Linux SO_PEERCRED is required")
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                         struct.calcsize("3i"))
    _pid, peer_uid, _gid = struct.unpack("3i", credentials)
    if peer_uid != allowed_uid:
        raise PermissionError("untrusted local monitor peer")
    connection.settimeout(2)
    chunks: list[bytes] = []
    size = 0
    while True:
        part = connection.recv(MAX_FRAME + 1 - size)
        if not part:
            break
        size += len(part)
        if size > MAX_FRAME:
            raise ValueError("monitor frame is too large")
        chunks.append(part)
    return validate_frame(b"".join(chunks))


def deliver(jobs: list[dict], secret: str, now: int | None = None) -> bool:
    """Send only to a compile-time HTTPS endpoint, with TLS hostname checks.

    http.client deliberately does not follow redirects or use proxy variables.
    Incoming socket frames supply job identities only, never a host or URL.
    """
    if len(secret) < 32:
        raise ValueError("monitor signing credential missing")
    body, signature = signed_body(jobs, secret, int(time.time()) if now is None else now)
    connection = http.client.HTTPSConnection(
        HOST, port=PORT, timeout=5, context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "POST", REPORT_PATH, body=body,
            headers={"Content-Type": "application/json",
                     "X-Wonjae-Monitor-Signature": signature},
        )
        response = connection.getresponse()
        response.read(256)
        return response.status == 202
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def load_credential(directory: str) -> str:
    """Read the systemd-protected credential file, never the environment value."""
    if not directory or not Path(directory).is_absolute():
        raise ValueError("systemd credentials directory required")
    secret = (Path(directory) / "monitor_secret").read_text(encoding="utf-8").strip()
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("monitor signing credential missing")
    return secret


def create_listener(path: Path) -> socket.socket:
    """Bind our own socket only; never remove a non-socket or a symlink."""
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("bridge runtime directory missing or redirected")
    if stat.S_IMODE(parent.stat().st_mode) & 0o022:
        raise PermissionError("bridge directory must not be writable by other users")
    if path.is_symlink():
        raise ValueError("bridge socket path is a symlink")
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise ValueError("refusing to overwrite a non-socket bridge file")
        path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        # Traversal is only through the trusted service's 0711 RuntimeDirectory.
        # Every peer is further restricted to the exact Runner uid (SO_PEERCRED).
        os.chmod(path, 0o666)
        listener.listen(4)
        listener.settimeout(2)
    except BaseException:
        listener.close()
        raise
    return listener


def serve(path: Path, runner_uid: int, secret: str) -> None:
    """At most one outbound request per change or 30s; no stale replay."""
    if len(secret) < 32:
        raise ValueError("monitor signing credential missing")
    running = True

    def stop(_signal: int, _frame: object) -> None:
        nonlocal running
        running = False

    original_term = signal.signal(signal.SIGTERM, stop)
    original_int = signal.signal(signal.SIGINT, stop)
    listener = create_listener(path)
    previous: str | None = None
    last_attempt = 0.0
    try:
        while running:
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            with connection:
                try:
                    jobs = read_peer_frame(connection, runner_uid)
                except (OSError, ValueError, PermissionError, RuntimeError):
                    continue
            key = json.dumps(jobs, sort_keys=True, separators=(",", ":"))
            elapsed = time.monotonic()
            if key != previous or elapsed - last_attempt >= REPORT_INTERVAL:
                try:
                    ok = deliver(jobs, secret)
                except ValueError:
                    ok = False
                if not ok:
                    print("MONITOR_BRIDGE_DELIVERY_UNAVAILABLE", flush=True)
                previous = key
                last_attempt = elapsed
    finally:
        listener.close()
        if path.is_socket() and not path.is_symlink():
            path.unlink()
        signal.signal(signal.SIGTERM, original_term)
        signal.signal(signal.SIGINT, original_int)


def main() -> int:
    parser = argparse.ArgumentParser(description="Restricted, fixed-target monitor bridge")
    parser.add_argument("--runner-user", required=True, help="exact local heartbeat Linux user")
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise ValueError("refusing to run network broker as root")
        runner_uid = pwd.getpwnam(args.runner_user).pw_uid
        secret = load_credential(os.environ.get("CREDENTIALS_DIRECTORY", ""))
        serve(SOCKET_PATH, runner_uid, secret)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError):
        print("MONITOR_BRIDGE_CONFIGURATION_ERROR", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
