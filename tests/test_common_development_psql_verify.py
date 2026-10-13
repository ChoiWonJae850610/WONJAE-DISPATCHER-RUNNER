"""Offline verification for shared pinned psql session/cleanup coordinator.

No real database, credentials, project dispatch or Provider changes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/common_development_psql_verify.py"
SPEC = importlib.util.spec_from_file_location("common_development_psql_verify", SOURCE)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

REF = "a" * 20


def git(root: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def repo_plan(tmp_path: Path, *, race: bool = True):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Synthetic")
    git(root, "config", "user.email", "synthetic@invalid.test")
    phases = ["preflight", "runtime", "cleanup", "zero_residue"]
    if race:
        phases += ["fixture_setup", "race_a", "race_b", "race_readback"]
    manifest = {
        "schema_version": 1, "project_ref": REF,
        "environment": "development", "phases": {},
        "variables": {"workspace_id": "00000000-0000-4000-8000-000000000001"},
        "expected_b_error": "SYNTHETIC_CONFLICT" if race else None,
    }
    for phase in phases:
        relative = f"supabase/tests/synthetic_{phase}.sql"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = f"SELECT '{phase}'::text;\n"
        path.write_text(data, encoding="utf-8")
        digest = hashlib.sha1(
            b"blob " + str(len(data.encode())).encode() + b"\0" + data.encode()
        ).hexdigest()
        manifest["phases"][phase] = {"path": relative, "blob": digest}
    name = "supabase/tests/synthetic_plan.json"
    path = root / name
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    git(root, "add", "supabase/tests")
    git(root, "commit", "-qm", "synthetic pinned psql plan")
    return root, git(root, "rev-parse", "HEAD"), name, git(root, "rev-parse", "HEAD:" + name)


def test_all_registered_sql_bytes_and_manifest_are_exact_git_blobs(tmp_path):
    root, sha, plan_path, blob = repo_plan(tmp_path)
    plan = m.load_plan(root, sha, REF, plan_path, blob)
    assert plan["environment"] == "development"
    assert len(plan["phases"]) == 8
    assert plan["expected_b_error"] == "SYNTHETIC_CONFLICT"


def test_no_source_drift_or_path_escape_is_allowed(tmp_path):
    root, sha, plan_path, blob = repo_plan(tmp_path, race=False)
    (root / "supabase/tests/synthetic_runtime.sql").write_text("SELECT 'changed';\n")
    with pytest.raises(m.VerificationBlocked, match="PSQL_SOURCE_BLOB_CHANGED"):
        m.load_plan(root, sha, REF, plan_path, blob)
    with pytest.raises(m.VerificationBlocked, match="PSQL_IDENTITY_INVALID"):
        m.load_plan(root, sha, "wrong", plan_path, blob)
    with pytest.raises(m.VerificationBlocked, match="PSQL_PIN_INVALID"):
        m.exact_file(root, sha, "../../private.sql", "b" * 40, plan=False)




@pytest.mark.parametrize("unsafe_sql", [
    r"\! echo forbidden",
    r"\i unpinned.sql",
    r"\setenv PGHOST attacker",
    r"\copy test to PROGRAM 'echo bad'",
    "COPY public.test TO PROGRAM 'echo bad';",
    r"\gexec",
    r"\set value `uname`",
    r"\set ON_ERROR_STOP off",
])
def test_even_pinned_scripts_cannot_execute_shell_or_override_psql_safety(
    tmp_path, unsafe_sql,
):
    root, _sha, plan_path, _blob = repo_plan(tmp_path, race=False)
    manifest_path = root / plan_path
    manifest = json.loads(manifest_path.read_text())
    runtime = root / manifest["phases"]["runtime"]["path"]
    text = unsafe_sql + "\n"
    runtime.write_text(text, encoding="utf-8")
    digest = hashlib.sha1(
        b"blob " + str(len(text.encode())).encode() + b"\0" + text.encode()
    ).hexdigest()
    manifest["phases"]["runtime"]["blob"] = digest
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    git(root, "add", "supabase/tests")
    git(root, "commit", "-qm", "attempt unsafe pinned psql script")
    sha = git(root, "rev-parse", "HEAD")
    blob = git(root, "rev-parse", "HEAD:" + plan_path)
    with pytest.raises(m.VerificationBlocked, match="PSQL_.*UNSAFE"):
        m.load_plan(root, sha, REF, plan_path, blob)

def test_runtime_plan_requires_full_pinned_cleanup_even_without_race(tmp_path):
    root, sha, plan_path, blob = repo_plan(tmp_path, race=False)
    manifest_file = root / plan_path
    manifest = json.loads(manifest_file.read_text())
    del manifest["phases"]["zero_residue"]
    manifest_file.write_text(json.dumps(manifest))
    git(root, "add", plan_path)
    git(root, "commit", "-qm", "invalid cleanup plan")
    new_sha = git(root, "rev-parse", "HEAD")
    new_blob = git(root, "rev-parse", "HEAD:" + plan_path)
    with pytest.raises(m.VerificationBlocked, match="PSQL_PLAN_PHASES_INVALID"):
        m.load_plan(root, new_sha, REF, plan_path, new_blob)


def test_connection_rejects_wrong_project_and_transaction_pooler(tmp_path):
    cert = tmp_path / "supabase-root.crt"
    cert.write_text("synthetic CA only")
    settings = {
        "WONJAE_DEV_PGHOST": f"db.{REF}.supabase.co",
        "WONJAE_DEV_PGUSER": "postgres",
        "WONJAE_DEV_PGPORT": "5432",
        "WONJAE_DEV_PGDATABASE": "postgres",
        "WONJAE_DEV_PGPASSWORD": "synthetic-hidden",
        "WONJAE_DEV_PGSSLROOTCERT": str(cert),
        "PATH": "/usr/bin",
    }
    env = m.connection_env(REF, settings)
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGPASSWORD"] == "synthetic-hidden"
    assert "WONJAE_DEV_PGPASSWORD" not in env
    assert env["PGHOST"] == f"db.{REF}.supabase.co"
    settings["WONJAE_DEV_PGHOST"] = "db." + "b" * 20 + ".supabase.co"
    with pytest.raises(m.VerificationBlocked, match="PSQL_TARGET_NOT_PROJECT_SCOPED"):
        m.connection_env(REF, settings)
    settings["WONJAE_DEV_PGHOST"] = "aws-0-region.pooler.supabase.com"
    settings["WONJAE_DEV_PGUSER"] = f"postgres.{REF}"
    settings["WONJAE_DEV_PGPORT"] = "6543"
    with pytest.raises(m.VerificationBlocked, match="PSQL_CONNECTION_FIELDS_INVALID"):
        m.connection_env(REF, settings)
    settings["WONJAE_DEV_PGPORT"] = "5432"
    assert m.connection_env(REF, settings)["PGUSER"] == f"postgres.{REF}"


class FakeRunner:
    def __init__(self, *, fail: str | None = None):
        self.calls = []
        self.fail = fail

    def file(self, path):
        name = path.name.removeprefix("synthetic_").removesuffix(".sql")
        self.calls.append(name)
        if name == self.fail:
            raise m.VerificationBlocked("PSQL_SCRIPT_FAILED")

    def race(self, a, b, readback, expected_error):
        self.calls.append("race")
        assert a.name.endswith("race_a.sql")
        assert b.name.endswith("race_b.sql")
        assert readback.name.endswith("race_readback.sql")
        assert expected_error == "SYNTHETIC_CONFLICT"
        if self.fail == "race":
            raise m.VerificationBlocked("PSQL_RACE_LOCK_NOT_OBSERVED")


def test_verified_runtime_and_distinct_session_race_then_cleanup(tmp_path):
    root, sha, plan_path, blob = repo_plan(tmp_path)
    plan = m.load_plan(root, sha, REF, plan_path, blob)
    fake = FakeRunner()
    receipt = m.verify(plan, root, sha, fake)
    assert receipt["result"] == "PSQL_DEVELOPMENT_VERIFIED"
    assert receipt["race"] == "PASS"
    assert receipt["cleanup"] == "PASS" and receipt["zero_residue"] == "PASS"
    assert fake.calls == [
        "preflight", "runtime", "fixture_setup", "race", "cleanup", "zero_residue"
    ]


@pytest.mark.parametrize("failed", ["runtime", "race", "cleanup", "zero_residue"])
def test_any_runtime_or_race_failure_runs_cleanup_and_zero_residue(tmp_path, failed):
    root, sha, plan_path, blob = repo_plan(tmp_path)
    plan = m.load_plan(root, sha, REF, plan_path, blob)
    fake = FakeRunner(fail=failed)
    receipt = m.verify(plan, root, sha, fake)
    assert receipt["result"] == "FAILED"
    assert fake.calls[-2:] == ["cleanup", "zero_residue"]
    if failed in ("cleanup", "zero_residue"):
        assert receipt[failed] == "FAILED"
    if failed == "race":
        assert receipt["race"] == "NOT_RUN"


def test_guard_failure_never_runs_mutating_cleanup_on_unverified_target(tmp_path):
    root, sha, plan_path, blob = repo_plan(tmp_path)
    plan = m.load_plan(root, sha, REF, plan_path, blob)
    fake = FakeRunner(fail="preflight")
    receipt = m.verify(plan, root, sha, fake)
    assert receipt["result"] == "FAILED"
    assert fake.calls == ["preflight"]
    assert receipt["cleanup"] == "NOT_RUN"


class FakeSession:
    def __init__(self, number, calls):
        self.number = number
        self.calls = calls
        self.done = False

    def pid(self):
        return self.number

    def include(self, path, barrier, wait=True):
        self.calls.append((self.number, "include", path.name, wait))

    def alive(self):
        return not self.done

    def send(self, command):
        self.calls.append((self.number, "send", command[:6]))

    def expect(self, barrier, timeout):
        self.calls.append((self.number, "expect", barrier))

    def finish(self, *, expected, marker=None):
        self.calls.append((self.number, "finish", expected, marker))
        self.done = True

    def abort(self):
        self.calls.append((self.number, "abort"))
        self.done = True


class FakeRaceRunner(m.PsqlRunner):
    def __init__(self, blocked=True):
        super().__init__({}, {})
        self.calls = []
        self.blocked = blocked
        self.sessions = []

    def new_session(self):
        instance = FakeSession([1001, 1002][len(self.sessions)], self.calls)
        self.sessions.append(instance)
        return instance

    def query(self, sql):
        if "state = 'idle in transaction'" in sql:
            return "t"
        if "pg_blocking_pids" in sql:
            return "t" if self.blocked else "f"
        raise AssertionError("Unexpected SQL probe")

    def file(self, path):
        self.calls.append(("readback", path.name))


def test_race_requires_independent_backend_pids_blocking_and_server_readback():
    runner = FakeRaceRunner()
    runner.race(Path("a.sql"), Path("b.sql"), Path("readback.sql"), "CONFLICT")
    assert len(runner.sessions) == 2
    assert runner.sessions[0].number != runner.sessions[1].number
    assert ("readback", "readback.sql") in runner.calls
    assert (1002, "finish", 3, "CONFLICT") in runner.calls
    assert (1001, "finish", 0, None) in runner.calls


def test_psql_binary_invocation_is_non_shell_and_stops_on_error(monkeypatch):
    monkeypatch.setattr(m.shutil, "which", lambda name: "/bin/psql")
    args = m.psql_command({"synthetic_var": "value"}, interactive=True)
    assert args[0] == "/bin/psql"
    assert "-X" in args and "ON_ERROR_STOP=1" in args
    assert "-f" in args and args[-1] == "-"
    assert "PGPASSWORD" not in " ".join(args)


def test_g1v_requires_both_real_race_phases_and_conflict_expectation(tmp_path):
    import sys

    def attempt(race):
        target = tmp_path / ("withrace" if race else "norace")
        target.mkdir()
        root, sha, plan_path, blob = repo_plan(target, race=race)
        return subprocess.run(
            [
                sys.executable, str(SOURCE),
                "--repo", str(root), "--source-sha", sha,
                "--project-ref", REF, "--plan-path", plan_path,
                "--plan-blob", blob,
                "--evidence", str(target / "receipt.json"),
                "--summary", str(target / "summary.txt"),
                "--require-race",
            ], capture_output=True, text=True, check=False,
        )

    prepared = attempt(True)
    assert prepared.returncode == 0, prepared.stderr
    assert "SOURCE_PLAN_VALIDATED_ONLY" in prepared.stdout
    assert not (tmp_path / "withrace" / "receipt.json").exists()

    incomplete = attempt(False)
    assert incomplete.returncode == 1
    assert "PSQL_INDEPENDENT_RACE_NOT_REGISTERED" in incomplete.stdout
    assert not (tmp_path / "norace" / "receipt.json").exists()


def test_classmo_g1v_registered_session_pooler_matches_owner_parameters(tmp_path):
    host = "aws-0-ap-northeast-2.pooler.supabase.com"
    target = m.registered_target(m.CLASSMO_G1V_REF, "session_pooler", host)
    assert target == (host, "postgres.hfclkbqzxiqhcckwknih")
    cert = tmp_path / "synthetic-test-ca.pem"
    cert.write_text("offline test certificate")
    cfg = {
        "WONJAE_DEV_PGHOST": host,
        "WONJAE_DEV_PGUSER": "postgres.hfclkbqzxiqhcckwknih",
        "WONJAE_DEV_PGPORT": "5432",
        "WONJAE_DEV_PGDATABASE": "postgres",
        "WONJAE_DEV_PGPASSWORD": "synthetic-local-test-value",
        "WONJAE_DEV_PGSSLROOTCERT": str(cert),
        "PGSERVICE": "untrusted",
        "PGOPTIONS": "-c search_path=public",
    }
    env = m.connection_env(
        m.CLASSMO_G1V_REF, cfg,
        expected_target=target,
        session_settings=m.CLASSMO_G1V_SETTINGS,
    )
    assert env["PGHOST"] == host
    assert env["PGUSER"] == "postgres.hfclkbqzxiqhcckwknih"
    assert env["PGPORT"] == "5432"
    assert env["PGDATABASE"] == "postgres"
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGSSLROOTCERT"] == str(cert)
    assert env["PGOPTIONS"] == (
        "-c classmo.g1v_authorized=CLASSMO-EDITORIAL-G1V-FIXTURE-001"
    )
    assert "PGSERVICE" not in env
    assert env["PGPASSWORD"] == "synthetic-local-test-value"
    assert "WONJAE_DEV_PGPASSWORD" not in env

    cfg["WONJAE_DEV_PGUSER"] = "postgres"
    with pytest.raises(m.VerificationBlocked, match="PSQL_TARGET_NOT_PROJECT_SCOPED"):
        m.connection_env(
            m.CLASSMO_G1V_REF, cfg, expected_target=target,
        )
    cfg["WONJAE_DEV_PGUSER"] = target[1]
    cfg["WONJAE_DEV_PGPORT"] = "6543"
    with pytest.raises(m.VerificationBlocked, match="PSQL_CONNECTION_FIELDS_INVALID"):
        m.connection_env(
            m.CLASSMO_G1V_REF, cfg, expected_target=target,
        )
    cfg["WONJAE_DEV_PGPORT"] = "5432"
    cfg["WONJAE_DEV_PGHOST"] = "aws-1-ap-northeast-2.pooler.supabase.com"
    with pytest.raises(m.VerificationBlocked, match="PSQL_TARGET_NOT_REGISTERED"):
        m.connection_env(
            m.CLASSMO_G1V_REF, cfg, expected_target=target,
        )


@pytest.mark.parametrize("project,mode,host,reason", [
    ("hfclkbqzxiqhcckwknih", "session_pooler",
     "aws-1-ap-northeast-2.pooler.supabase.com", "PSQL_POOLER_TARGET_NOT_REGISTERED"),
    ("hfclkbqzxiqhcckwknih", "session_pooler",
     "aws-0-ap-northeast-1.pooler.supabase.com", "PSQL_POOLER_TARGET_NOT_REGISTERED"),
    ("hfclkbqzxiqhcckwknih", "session_pooler",
     "aws-0-ap-northeast-2.pooler.supabase.com:6543", "PSQL_POOLER_TARGET_NOT_REGISTERED"),
    ("aaaaaaaaaaaaaaaaaaaa", "session_pooler",
     "aws-0-ap-northeast-2.pooler.supabase.com", "PSQL_POOLER_TARGET_NOT_REGISTERED"),
    ("hfclkbqzxiqhcckwknih", "transaction_pooler",
     "aws-0-ap-northeast-2.pooler.supabase.com", "PSQL_CONNECTION_MODE_NOT_REGISTERED"),
    ("hfclkbqzxiqhcckwknih", "direct",
     "aws-0-ap-northeast-2.pooler.supabase.com", "PSQL_UNEXPECTED_POOLER_HOST"),
])
def test_unregistered_psql_routes_cannot_connect(project, mode, host, reason):
    with pytest.raises(m.VerificationBlocked, match=reason):
        m.registered_target(project, mode, host)


def test_direct_mode_keeps_legacy_server_and_exact_identity():
    assert m.registered_target("a" * 20, "direct", "") == (
        "db." + "a" * 20 + ".supabase.co", "postgres"
    )
