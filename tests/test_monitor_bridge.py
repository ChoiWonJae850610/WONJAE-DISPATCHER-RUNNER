"""Offline unit tests for the fixed-target AF_UNIX monitor bridge."""
from __future__ import annotations

import importlib
import json
import os
import socket
import ssl
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
bridge = importlib.import_module("monitor_bridge")
monitor = importlib.import_module("local_monitor")

JOB = {
    "project": "WAFL", "runId": 564, "runAttempt": 2,
    "sourceSha": "b" * 40, "phase": "CODEX", "state": "running",
}


def make_frame(jobs: list[dict] | None = None) -> bytes:
    return json.dumps({"version": 1, "host": "home-linux",
                       "jobs": [JOB] if jobs is None else jobs}).encode("utf-8")


def test_valid_fixed_schema_and_empty_jobs():
    assert bridge.validate_frame(make_frame()) == [JOB]
    assert bridge.validate_frame(make_frame([])) == []


@pytest.mark.parametrize("bad", [
    b"", b"not json", b"{" + b"X" * 5000 + b"}",
    b'{"version":1,"host":"wrong","jobs":[]}',
    b'{"version":1,"host":"home-linux","jobs":[],"endpoint":"https://attacker"}',
])
def test_reject_malformed_or_arbitrary_target(bad: bytes):
    with pytest.raises(ValueError):
        bridge.validate_frame(bad)


@pytest.mark.parametrize("change", [
    {"project": "KDN"},
    {"project": "wafl"},
    {"runId": True},
    {"runId": 0},
    {"runId": 9007199254740992},
    {"runAttempt": "1"},
    {"sourceSha": "f" * 39},
    {"phase": "DEPLOY"},
    {"state": "FAILED"},
    {"state": []},
    {"state": {"nested": "untrusted"}},
    {"target": "https://attacker"},
])
def test_reject_untrusted_job_fields(change: dict):
    with pytest.raises(ValueError):
        bridge.validate_frame(make_frame([{**JOB, **change}]))


def test_duplicate_job_and_job_count_are_rejected():
    with pytest.raises(ValueError):
        bridge.validate_frame(make_frame([JOB, JOB]))
    with pytest.raises(ValueError):
        bridge.validate_frame(make_frame([{**JOB, "runId": x + 1} for x in range(17)]))


def test_unix_peer_authentication_and_actual_producer(tmp_path: Path):
    target = tmp_path / "relay.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(target))
    server.listen(1)
    try:
        monitor.publish_bridge_snapshot([JOB], str(target))
        peer, _ = server.accept()
        with peer:
            assert bridge.read_peer_frame(peer, os.getuid()) == [JOB]
        monitor.publish_bridge_snapshot([], str(target))
        peer, _ = server.accept()
        with peer:
            with pytest.raises(PermissionError):
                bridge.read_peer_frame(peer, os.getuid() + 1)
    finally:
        server.close()


def test_tls_fixed_host_post_and_redirect_is_never_followed(monkeypatch):
    calls = []

    class FakeReply:
        status = 202

        def read(self, limit):
            assert limit == 256
            return b""

    class FakeConnection:
        def __init__(self, host, port, timeout, context):
            calls.append(("connect", host, port, timeout, context.verify_mode,
                          context.check_hostname))

        def request(self, method, path, body, headers):
            calls.append(("request", method, path, body, headers))

        def getresponse(self):
            return FakeReply()

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(bridge.http.client, "HTTPSConnection", FakeConnection)
    secret = "synthetic-restricted-monitor-key-over-32-chars"
    assert bridge.deliver([JOB], secret, 1800000000)
    assert calls[0][:4] == ("connect", "sanjinworks.com", 443, 5)
    assert calls[0][4:] == (ssl.CERT_REQUIRED, True)
    assert calls[1][1:3] == ("POST", "/api/monitor/report")
    assert calls[1][4]["X-Wonjae-Monitor-Signature"].startswith("sha256=")
    assert secret.encode() not in calls[1][3]
    assert calls[-1] == ("close",)

    FakeReply.status = 302
    assert not bridge.deliver([], secret, 1800000000)
    assert len([c for c in calls if c[0] == "request"]) == 2


def test_credential_only_from_protected_file(tmp_path: Path):
    token = "synthetic-valid-secret-of-at-least-32-chars"
    (tmp_path / "monitor_secret").write_text(token)
    assert bridge.load_credential(str(tmp_path)) == token
    with pytest.raises(ValueError):
        bridge.load_credential("")
    (tmp_path / "monitor_secret").write_text("short")
    with pytest.raises(ValueError):
        bridge.load_credential(str(tmp_path))


def test_socket_refuses_symlink_and_non_socket(tmp_path: Path):
    symlink = tmp_path / "relay.sock"
    symlink.symlink_to(tmp_path / "victim")
    with pytest.raises(ValueError):
        bridge.create_listener(symlink)
    symlink.unlink()
    symlink.write_text("do not overwrite")
    with pytest.raises(ValueError):
        bridge.create_listener(symlink)
    assert symlink.read_text() == "do not overwrite"


def test_bridge_socket_path_never_enables_direct_https_on_watcher():
    assert bridge.HOST == "sanjinworks.com"
    assert bridge.REPORT_PATH == "/api/monitor/report"
    assert monitor.BRIDGE_SOCKET == str(bridge.SOCKET_PATH)
