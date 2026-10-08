"""Synthetic offline liveness tests; no GitHub token, provider or network access."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from importlib.util import module_from_spec, spec_from_file_location

_spec = spec_from_file_location(
    "local_monitor", Path(__file__).resolve().parents[1] / "scripts" / "local_monitor.py",
)
assert _spec is not None and _spec.loader is not None
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)

atomic_write = _module.atomic_write
inspect_job = _module.inspect_job
private_directory = _module.private_directory
proc_start_ticks = _module.proc_start_ticks
same_process = _module.same_process
signed_body = _module.signed_body
snapshot = _module.snapshot


def test_private_directory_rejects_symlink_and_world_readable(tmp_path: Path):
    good = tmp_path / "private"
    private_directory(good)
    assert good.stat().st_mode & 0o077 == 0
    link = tmp_path / "link"
    link.symlink_to(good)
    with pytest.raises(ValueError):
        private_directory(link)
    bad = tmp_path / "world"
    bad.mkdir(mode=0o755)
    os.chmod(bad, 0o755)
    with pytest.raises(PermissionError):
        private_directory(bad)


def test_pid_start_ticks_prevent_pid_reuse_and_zombie_claims():
    import os
    ticks = proc_start_ticks(os.getpid())
    assert ticks and same_process(os.getpid(), ticks)
    assert not same_process(os.getpid(), "0")
    assert not same_process(-1, ticks)


def test_abnormal_vs_completed_without_changing_official_result(tmp_path: Path):
    private_directory(tmp_path)
    job = tmp_path / "9876-2"
    private_directory(job)
    now = int(time.time())
    meta = {
        "project": "CLASSMO", "runId": 9876, "runAttempt": 2,
        "sourceSha": "a" * 40, "startedAt": now - 20, "phase": "CODEX",
        "supervisorPid": os.getpid(), "supervisorStart": proc_start_ticks(os.getpid()),
        "childPid": os.getpid(), "childStart": proc_start_ticks(os.getpid()),
    }
    atomic_write(job / "meta.json", json.dumps(meta))
    atomic_write(job / "heartbeat", str(now) + "\n")
    assert inspect_job(job, now)["state"] == "running"
    assert inspect_job(job, now + 16)["state"] == "abnormal"
    atomic_write(job / "result.json", json.dumps({
        "state": "completed", "exitCode": 0, "completedAt": now,
    }))
    assert inspect_job(job, now + 20)["state"] == "completed"
    assert inspect_job(job, now + 121) is None
    assert len(snapshot(tmp_path, now)) == 1


def test_supervised_process_records_bounded_meta_and_zero_exit(tmp_path: Path):
    cmd = [
        sys.executable, "scripts/local_monitor.py", "supervise",
        "--root", str(tmp_path / "private"),
        "--project", "WAFL", "--run-id", "456", "--run-attempt", "1",
        "--source-sha", "b" * 40, "--",
        sys.executable, "-c", "print('synthetic bounded child')",
    ]
    output = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert output.returncode == 0, output.stderr
    meta = json.loads((tmp_path / "private/456-1/meta.json").read_text())
    result = json.loads((tmp_path / "private/456-1/result.json").read_text())
    assert meta["project"] == "WAFL"
    assert meta["sourceSha"] == "b" * 40
    assert meta["runAttempt"] == 1
    assert result["state"] == "completed"
    assert result["exitCode"] == 0
    assert isinstance(result["completedAt"], int)
    assert not (tmp_path / "private/456-1/auth.json").exists()


def test_failed_child_does_not_turn_into_github_pass(tmp_path: Path):
    cmd = [
        sys.executable, "scripts/local_monitor.py", "supervise",
        "--root", str(tmp_path / "private"), "--project", "MUVEL",
        "--run-id", "998", "--run-attempt", "1",
        "--source-sha", "c" * 40, "--",
        sys.executable, "-c", "raise SystemExit(19)",
    ]
    result = subprocess.run(cmd, timeout=15)
    assert result.returncode == 19
    saved = json.loads((tmp_path / "private/998-1/result.json").read_text())
    assert saved["state"] == "error"
    assert saved["exitCode"] == 19


def test_signed_payload_discloses_no_secret():
    secret = "synthetic-monitor-key-with-at-least-32-bytes"
    body, signature = signed_body([], secret, 1760000000)
    assert b"synthetic-monitor-key" not in body
    assert signature.startswith("sha256=")
    assert len(signature) == 71
    assert json.loads(body)["host"] == "home-linux"
