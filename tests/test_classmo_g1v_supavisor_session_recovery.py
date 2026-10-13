"""Offline regression for failed G1-V Supavisor preflight (run #38349605506).

No database, network, token, Secret or real psql process is used.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SOURCE = Path("scripts/common_development_psql_verify.py")
SPEC = importlib.util.spec_from_file_location("common_psql_guc_repair", SOURCE)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def test_preflight_file_and_probe_set_fixed_session_guc_before_sql(monkeypatch):
    seen = []
    monkeypatch.setattr(m.shutil, "which", lambda _name: "/offline/psql")

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="t\n", stderr="")

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    env = {"PGOPTIONS": m.G1V_PGOPTIONS, "PGPASSWORD": "synthetic"}
    runner = m.PsqlRunner(env, {})
    runner.file(Path("/offline/editorial_g1v_preflight.sql"))
    assert runner.query("select true;") == "t"
    assert len(seen) == 2
    file_argv, file_kw = seen[0]
    query_argv, query_kw = seen[1]
    for argv, kw in seen:
        idx = argv.index(m.G1V_SESSION_SET)
        assert argv[idx - 1] == "-c"
        assert kw["env"] is env
        assert "PGPASSWORD" not in " ".join(argv)
        assert "synthetic" not in " ".join(argv)
    assert file_argv.index(m.G1V_SESSION_SET) < file_argv.index("-f")
    assert query_argv.index(m.G1V_SESSION_SET) < query_argv.index("select true;")


def test_no_custom_guc_for_other_registered_plain_psql_plans(monkeypatch):
    seen = []
    monkeypatch.setattr(m.shutil, "which", lambda _name: "/offline/psql")

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="t\n", stderr="")

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    runner = m.PsqlRunner({"PGHOST": "offline"}, {})
    runner.file(Path("/offline/other_preflight.sql"))
    assert m.G1V_SESSION_SET not in seen[0]
    with pytest.raises(
        m.VerificationBlocked, match="PSQL_SESSION_SETTINGS_NOT_REGISTERED"
    ):
        m.PsqlRunner({"PGOPTIONS": "-c search_path=public"}, {})


class OfflineProcess:
    def __init__(self, argv):
        self.argv = argv
        self.stdin = object()
        self.stdout = iter(())
        self.stderr = iter(())
        self.running = True
        self.terminated = False

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.running = False
        self.terminated = True

    def wait(self, timeout=5):
        self.running = False
        return 0


class OfflineThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


@pytest.mark.parametrize("server_answer,expected_error", [
    ("t", None),
    ("f", "PSQL_SESSION_GUC_UNVERIFIED"),
])
def test_independent_backend_requires_explicit_server_guc_readback(
    monkeypatch, server_answer, expected_error,
):
    events = []
    instances = []
    monkeypatch.setattr(m.shutil, "which", lambda _name: "/offline/psql")

    def fake_popen(argv, **kwargs):
        proc = OfflineProcess(argv)
        instances.append(proc)
        assert kwargs["env"]["PGOPTIONS"] == m.G1V_PGOPTIONS
        return proc

    monkeypatch.setattr(m.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(m.threading, "Thread", OfflineThread)
    monkeypatch.setattr(m.PsqlSession, "send",
                        lambda _self, command: events.append(command))
    monkeypatch.setattr(m.PsqlSession, "expect",
                        lambda _self, prefix, timeout: (
                            events.append(prefix) or server_answer
                        ))
    env = {"PGOPTIONS": m.G1V_PGOPTIONS}
    if expected_error:
        with pytest.raises(m.VerificationBlocked, match=expected_error):
            m.PsqlSession(env, {}, m.G1V_SESSION_SET)
        assert instances[0].terminated
    else:
        session = m.PsqlSession(env, {}, m.G1V_SESSION_SET)
        assert session.proc is instances[0]
    assert events[0].startswith(m.G1V_SESSION_SET + "\n")
    assert "current_setting('classmo.g1v_authorized', true)" in events[0]
    assert "\\gset" in events[0]
    assert "__WONJAE_GUC_READY__" in events[1]
    assert "PGPASSWORD" not in events[0]


def test_failure_receipt_codes_never_echo_raw_postgres_stderr(monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda _name: "/offline/psql")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 3, stdout="",
            stderr="ERROR: EDITORIAL_G1V_AUTHORIZATION_REQUIRED\n"
                   "sensitive raw connection context: private",
        )

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    runner = m.PsqlRunner({"PGOPTIONS": m.G1V_PGOPTIONS}, {})
    with pytest.raises(m.VerificationBlocked) as error:
        runner.file(Path("/offline/preflight.sql"))
    assert str(error.value) == "PSQL_G1V_AUTHORIZATION_FAILED"
    assert "private" not in str(error.value)
    assert m.psql_failure_code("unexpected private details") == "PSQL_SCRIPT_FAILED"
