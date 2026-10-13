"""Offline G1-V contract reconciliation using the exact CLASSMO manifest shape.

All SQL files here are synthetic and privately created under tmp_path.
No product checkout, DB connection, PG password, Owner gate, or network access.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

MODULE = Path("scripts/common_development_psql_verify.py")
SPEC = importlib.util.spec_from_file_location("shared_psql_contract", MODULE)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def git(root: Path, *args: str) -> str:
    output = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return output.stdout.strip()


def classmo_style_repo(tmp_path: Path, *, old_blob_keys: bool = False):
    """Match 2026-10-13 CLASSMO phase/contract JSON keys with synthetic SQL."""
    repo = tmp_path / "product"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic")
    git(repo, "config", "user.email", "synthetic@invalid.test")
    phases = {}
    for phase in (
        "preflight", "runtime", "fixture_setup", "race_a", "race_b",
        "race_readback", "cleanup", "zero_residue",
    ):
        path = f"supabase/tests/synthetic_{phase}.sql"
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = "select 'phase: " + phase + "'::text;\n"
        target.write_text(contents, encoding="utf-8")
        raw = contents.encode()
        blob = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw
        ).hexdigest()
        phases[phase] = {
            "path": path, ("blob" if old_blob_keys else "git_blob"): blob
        }
    plan = {
        "schema_version": 1,
        "source_task_id": m.CLASSMO_G1V_TASK,
        "environment": "development",
        "project_ref": m.CLASSMO_G1V_REF,
        "expected_b_error": "EDITORIAL_DRAFT_REVISION_CONFLICT",
        "phases": phases,
        "execution_contract": {
            "authorization": "Separate authenticated Owner G1-V only.",
            "psql_on_error_stop": True,
            "session_user": "postgres",
            "required_session_settings": dict(m.CLASSMO_G1V_SETTINGS),
            "race_isolation": "read committed",
            "distinct_backend_sessions": True,
            "barrier": "two independent backend PIDs and observed blocker",
            "failure_cleanup": "exact-ID cleanup, independent residue proof",
        },
        "fixture": {"actor_id": "e0160000-0000-4000-8000-000000000101"},
    }
    relative = "supabase/tests/editorial_g1v_plan.json"
    (repo / relative).write_text(json.dumps(plan, indent=2) + "\n")
    return commit(repo, relative)


def commit(repo: Path, plan_path: str):
    git(repo, "add", "supabase/tests")
    git(repo, "commit", "-qm", "synthetic exact CLASSMO G1-V contract")
    sha = git(repo, "rev-parse", "HEAD")
    blob = git(repo, "rev-parse", "HEAD:" + plan_path)
    return repo, sha, plan_path, blob


def edit_plan(repo: Path, path: str, edit):
    file = repo / path
    plan = json.loads(file.read_text())
    edit(plan)
    file.write_text(json.dumps(plan, indent=2) + "\n")
    return commit(repo, path)


def load_repo(value):
    repo, sha, path, blob = value
    return m.load_plan(repo, sha, m.CLASSMO_G1V_REF, path, blob)


def settings(tmp_path: Path):
    cert = tmp_path / "synthetic-ca.crt"
    cert.write_text("offline dummy, never used for a connection")
    return {
        "WONJAE_DEV_PGHOST": f"db.{m.CLASSMO_G1V_REF}.supabase.co",
        "WONJAE_DEV_PGUSER": "postgres",
        "WONJAE_DEV_PGPORT": "5432",
        "WONJAE_DEV_PGDATABASE": "postgres",
        "WONJAE_DEV_PGPASSWORD": "synthetic-not-a-secret",
        "WONJAE_DEV_PGSSLROOTCERT": str(cert),
        "PGOPTIONS": "-c search_path=public -c statement_timeout=0",
        "PGSERVICE": "other-project",
        "PATH": "/usr/bin",
    }


@pytest.mark.parametrize("old_blob_keys", [False, True])
def test_exact_git_blob_or_legacy_blob_manifest_is_verified(tmp_path, old_blob_keys):
    product = classmo_style_repo(tmp_path, old_blob_keys=old_blob_keys)
    plan = load_repo(product)
    assert len(plan["phases"]) == 8
    assert m.registered_session_settings(plan) == m.CLASSMO_G1V_SETTINGS


def test_git_blob_mismatch_cannot_bypass_source_pin(tmp_path):
    root, _sha, path, _blob = classmo_style_repo(tmp_path)
    changed = edit_plan(root, path, lambda p: p["phases"]["race_a"].update(
        git_blob="b" * 40
    ))
    with pytest.raises(m.VerificationBlocked, match="PSQL_SOURCE_BLOB_CHANGED"):
        load_repo(changed)


@pytest.mark.parametrize("keys", [
    {"path", "blob", "git_blob"}, {"path", "git_blob", "PGOPTIONS"},
    {"path"}, {"path", "git_blob", "shell"},
])
def test_extra_or_missing_phase_keys_are_rejected(tmp_path, keys):
    root, _sha, path, _blob = classmo_style_repo(tmp_path)

    def bad(p):
        original = p["phases"]["race_b"]
        p["phases"]["race_b"] = {
            k: original.get(k, "unsafe") for k in keys
        }

    changed = edit_plan(root, path, bad)
    with pytest.raises(m.VerificationBlocked, match="PSQL_PHASE_PIN_INVALID"):
        load_repo(changed)


@pytest.mark.parametrize("injected", [
    {"search_path": "public"},
    {"classmo.g1v_authorized": "fake"},
    {"classmo.g1v_authorized": "CLASSMO-EDITORIAL-G1V-FIXTURE-001 -c fsync=off"},
    {"classmo.g1v_authorized": "CLASSMO-EDITORIAL-G1V-FIXTURE-001",
     "statement_timeout": "0"},
    {"classmo.g1v_authorized": "CLASSMO-EDITORIAL-G1V-FIXTURE-001\n-c fsync=off"},
])
def test_unregistered_server_session_options_are_rejected_before_connection(
    tmp_path, injected,
):
    root, _sha, path, _blob = classmo_style_repo(tmp_path)
    changed = edit_plan(
        root, path,
        lambda p: p["execution_contract"].update(required_session_settings=injected),
    )
    with pytest.raises(m.VerificationBlocked, match="PSQL_SESSION_SETTINGS_NOT_REGISTERED"):
        load_repo(changed)


@pytest.mark.parametrize("field,value", [
    ("source_task_id", "OTHER-TASK"),
    ("project_ref", "b" * 20),
])
def test_classmo_guc_cannot_be_reused_for_another_identity(tmp_path, field, value):
    root, _sha, path, _blob = classmo_style_repo(tmp_path)
    changed = edit_plan(root, path, lambda p: p.update({field: value}))
    with pytest.raises(m.VerificationBlocked):
        load_repo(changed)


def test_other_products_have_no_session_option_injection(tmp_path):
    source = classmo_style_repo(tmp_path)
    base = load_repo(source)
    original = settings(tmp_path)
    other_project = "b" * 20
    original["WONJAE_DEV_PGHOST"] = f"db.{other_project}.supabase.co"
    env = m.connection_env(other_project, original)
    assert "PGOPTIONS" not in env
    with pytest.raises(m.VerificationBlocked, match="PSQL_SESSION_SETTINGS_NOT_REGISTERED"):
        m.connection_env(other_project, original, session_settings=m.CLASSMO_G1V_SETTINGS)
    assert m.registered_session_settings(base) == m.CLASSMO_G1V_SETTINGS


def test_registered_classmo_guc_reaches_every_new_backend_with_no_cli_injection(
    tmp_path, monkeypatch,
):
    plan = load_repo(classmo_style_repo(tmp_path))
    env = m.connection_env(
        m.CLASSMO_G1V_REF, settings(tmp_path),
        session_settings=m.registered_session_settings(plan),
    )
    expected = "-c classmo.g1v_authorized=CLASSMO-EDITORIAL-G1V-FIXTURE-001"
    assert env["PGOPTIONS"] == expected
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGHOST"] == f"db.{m.CLASSMO_G1V_REF}.supabase.co"
    assert env["PGPASSWORD"] == "synthetic-not-a-secret"
    assert "PGSERVICE" not in env

    observed = []
    monkeypatch.setattr(m.shutil, "which", lambda _name: "/synthetic/psql")

    def fake_exec(self, args, timeout=m.STEP_TIMEOUT):
        observed.append(("sql_or_probe", self.env, args))
        return "t"

    monkeypatch.setattr(m.PsqlRunner, "_exec", fake_exec)

    class FakeSession:
        def __init__(self, given_env, variables, initial_sql):
            observed.append(("independent_session", given_env, initial_sql))

    monkeypatch.setattr(m, "PsqlSession", FakeSession)
    runner = m.PsqlRunner(env, {})
    assert runner.initial_sql == m.G1V_SESSION_SET
    runner.file(Path("/offline/synthetic_preflight.sql"))
    runner.file(Path("/offline/synthetic_runtime.sql"))
    runner.query("select pg_blocking_pids(99);")
    runner.new_session()
    runner.new_session()
    runner.file(Path("/offline/synthetic_cleanup.sql"))
    runner.file(Path("/offline/synthetic_zero_residue.sql"))
    assert len(observed) == 7
    assert all(item[1] is env and item[1]["PGOPTIONS"] == expected for item in observed)
    for kind, _environment, payload in observed:
        if kind == "sql_or_probe":
            assert "-c" in payload
            assert m.G1V_SESSION_SET in payload
            assert "PGPASSWORD" not in " ".join(payload)
            assert "PGOPTIONS" not in " ".join(payload)
        else:
            assert payload == m.G1V_SESSION_SET


def test_connection_env_rejects_direct_arbitrary_setting_even_with_classmo_target(
    tmp_path,
):
    config = settings(tmp_path)
    with pytest.raises(m.VerificationBlocked, match="PSQL_SESSION_SETTINGS_NOT_REGISTERED"):
        m.connection_env(m.CLASSMO_G1V_REF, config, session_settings={
            "classmo.g1v_authorized": "CLASSMO-EDITORIAL-G1V-FIXTURE-001",
            "search_path": "public",
        })


def test_offline_classmo_git_blob_plan_and_registered_ipv4_route_without_db(
    tmp_path,
):
    import sys

    repo, sha, plan_path, blob = classmo_style_repo(tmp_path)
    evidence = tmp_path / "no-runtime-execution.json"
    summary = tmp_path / "no-runtime-summary.md"
    argv = [
        sys.executable, str(MODULE), "--repo", str(repo),
        "--source-sha", sha, "--project-ref", m.CLASSMO_G1V_REF,
        "--plan-path", plan_path, "--plan-blob", blob,
        "--evidence", str(evidence), "--summary", str(summary),
        "--connection-mode", "session_pooler",
        "--pooler-host", "aws-0-ap-northeast-2.pooler.supabase.com",
        "--require-race",
    ]
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PSQL_VERIFIER=SOURCE_PLAN_VALIDATED_ONLY" in result.stdout
    assert not evidence.exists()
    assert not summary.exists()
    argv[argv.index("aws-0-ap-northeast-2.pooler.supabase.com")] = (
        "aws-0-ap-northeast-1.pooler.supabase.com"
    )
    blocked = subprocess.run(argv, capture_output=True, text=True, check=False)
    assert blocked.returncode == 1
    assert "PSQL_POOLER_TARGET_NOT_REGISTERED" in blocked.stdout
    assert not evidence.exists()
