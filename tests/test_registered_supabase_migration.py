"""Offline tests for the registered generic Supabase migration adapter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/registered_supabase_migration.py"
SPEC = importlib.util.spec_from_file_location("registered_supabase_migration", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def committed_migration(tmp_path: Path):
    repo = tmp_path / "product"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "synthetic@invalid.test")
    git(repo, "config", "user.name", "Synthetic Test")
    path = "supabase/migrations/202610130001_new_schema.sql"
    target = repo / path
    target.parent.mkdir(parents=True)
    sql = "BEGIN;\nCREATE TABLE public.fixture (id integer);\nCOMMIT;\n"
    target.write_text(sql, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-qm", "synthetic migration")
    return repo, git(repo, "rev-parse", "HEAD"), path, git(repo, "rev-parse", f"HEAD:{path}"), sql


class FakeClient:
    ref = "a" * 20

    def __init__(self, *, prior="015_prior", already=False):
        self.history = [("20261007000000", prior)]
        if already:
            self.history.append(("20261013000001", "016_new"))
        self.applies = []

    def project(self):
        return None

    def ledger(self):
        return list(self.history)

    def apply(self, name, sql):
        assert name == "016_new"
        self.applies.append(sql)
        self.history.append(("20261013000001", name))


def test_exact_registered_blob_sends_original_sql_and_preserves_ledger(tmp_path):
    repo, sha, path, blob, sql = committed_migration(tmp_path)
    client = FakeClient()
    result = m.execute(client, repo, sha, path, blob, "016_new", "015_prior")
    assert client.applies == [sql]
    assert result["result"] == "REGISTERED_SCHEMA_APPLIED_RUNTIME_NOT_VERIFIED"
    assert result["source_git_blob"] == blob
    assert result["sql_sha256"] == hashlib.sha256(sql.encode()).hexdigest()
    assert result["migration_version"] == "20261013000001"
    assert result["runtime_psql"] == "NOT_RUN"
    assert result["synthetic_cleanup"] == "NOT_RUN"


@pytest.mark.parametrize("mode", ["different_sha", "different_blob", "path", "changed_bytes"])
def test_no_external_apply_on_wrong_exact_source(tmp_path, mode):
    repo, sha, path, blob, _sql = committed_migration(tmp_path)
    if mode == "different_sha":
        sha = "f" * 40
    elif mode == "different_blob":
        blob = "b" * 40
    elif mode == "path":
        path = "../../invalid.sql"
    else:
        (repo / path).write_text("SELECT 'changed';", encoding="utf-8")
    client = FakeClient()
    with pytest.raises(m.Blocked):
        m.execute(client, repo, sha, path, blob, "016_new", "015_prior")
    assert not client.applies


@pytest.mark.parametrize("prior,already", [("014_old", False), ("015_prior", True)])
def test_mismatched_or_already_applied_ledger_blocks_without_replay(tmp_path, prior, already):
    repo, sha, path, blob, _sql = committed_migration(tmp_path)
    client = FakeClient(prior=prior, already=already)
    with pytest.raises(m.Blocked, match="REGISTERED_MIGRATION_HISTORY_NOT_READY"):
        m.execute(client, repo, sha, path, blob, "016_new", "015_prior")
    assert client.applies == []


def test_post_apply_ledger_mismatch_does_not_fake_runtime_pass(tmp_path):
    repo, sha, path, blob, _sql = committed_migration(tmp_path)
    client = FakeClient()

    def apply_but_no_ledger(name, sql):
        client.applies.append(sql)

    client.apply = apply_but_no_ledger
    with pytest.raises(m.Blocked, match="REGISTERED_MIGRATION_POST_LEDGER_UNVERIFIED"):
        m.execute(client, repo, sha, path, blob, "016_new", "015_prior")
    assert len(client.applies) == 1


def test_client_only_uses_exact_metadata_read_and_migration_post(monkeypatch):
    calls = []
    token = "private-synthetic-secret"

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def fake_network(request, timeout):
        assert timeout == 60
        assert request.get_header("Authorization") == "Bearer " + token
        calls.append((request.get_method(), request.full_url))
        if request.get_method() == "GET":
            return Response({"id": "a" * 20})
        if request.full_url.endswith("/database/query/read-only"):
            return Response([{"version": "1", "name": "015_prior"}])
        return Response({"status": "success"})

    monkeypatch.setattr(m, "urlopen", fake_network)
    client = m.Client("a" * 20, token)
    client.project()
    assert client.ledger() == [("1", "015_prior")]
    client.apply("016_new", "BEGIN; COMMIT;")
    assert calls == [
        ("GET", "https://api.supabase.com/v1/projects/" + "a" * 20),
        ("POST", "https://api.supabase.com/v1/projects/" + "a" * 20
         + "/database/query/read-only"),
        ("POST", "https://api.supabase.com/v1/projects/" + "a" * 20
         + "/database/migrations"),
    ]
    with pytest.raises(m.Blocked, match="REGISTERED_MIGRATION_API_DENIED"):
        client._request("/database/query", {"query": "delete from auth.users"})


def test_http_403_write_reports_only_safe_capability(monkeypatch):
    secret = "synthetic-do-not-log"
    client = m.Client("a" * 20, secret)

    def denied(req, timeout):
        assert timeout == 60
        raise HTTPError(req.full_url, 403, "secret " + secret, {}, None)

    monkeypatch.setattr(m, "urlopen", denied)
    with pytest.raises(m.Blocked) as error:
        client.apply("016_new", "BEGIN; COMMIT;")
    assert str(error.value) == "REGISTERED_MIGRATION_HTTP_403_MIGRATIONS_WRITE"
    assert secret not in str(error.value)


def test_missing_token_and_invalid_project_do_not_call_provider():
    with pytest.raises(m.Blocked, match="CREDENTIAL_MISSING"):
        m.Client("a" * 20, "")
    with pytest.raises(m.Blocked, match="PROJECT_INVALID"):
        m.Client("unknown", "fake")


def test_workflow_is_scoped_to_registered_nonproduction_gate():
    workflow = (
        Path(__file__).resolve().parents[1] /
        ".github/workflows/provider-gate-approval-core.yml"
    ).read_text(encoding="utf-8")
    assert (
        '"registered_supabase_migration_v1", '
        '"registered_development_psql_verify_v1", '
        '"registered_development_readiness_v1", '
        '"wafl_g1v1v2_preparation_guard_v1", '
        '"classmo_editorial_027_p2_live_guard_v1", '
        '"wafl_development_media_probe_v1", '
        '"wafl_development_media_bootstrap_v1", '
        '"wafl_development_media_readonly_preflight_v1", '
        '"wafl_vercel_preview_env_bridge_v1", '
        '"wafl_pdf_dev_worker_only_v1"].include?(executor)' in workflow
    )
    for needle in (
        'config["environment"] == "development"',
        'config["ready_for_owner"] == true',
        'config["production"] == false',
        'config["destructive"] == false',
        "scripts/registered_supabase_migration.py",
        "REGISTERED_SUPABASE_ACCESS_TOKEN:",
        "registered-supabase-migration.json",
    ):
        assert needle in workflow
