"""No-provider/offline regression for the bounded CLASSMO Development 017 executor."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/classmo_editorial_017_storage_apply.py"
SPEC = importlib.util.spec_from_file_location("classmo_editorial_017_storage_apply", SCRIPT)
assert SPEC and SPEC.loader
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, check=True)
    return p.stdout.strip()


def source(tmp_path, monkeypatch):
    repo = tmp_path / "synthetic-product"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic")
    git(repo, "config", "user.email", "synthetic@invalid.test")
    sql = "-- synthetic-only\nselect 1;\n"
    file = repo / m.PATH
    file.parent.mkdir(parents=True)
    file.write_text(sql)
    digest = hashlib.sha256(sql.encode()).hexdigest()
    prefix = [
        "a" * 64 + f"  2026092900{n:04d}_0{n:02d}_test.sql"
        for n in range(1, 16)
    ]
    prefix.append("b" * 64 + "  20261012001600_016_editorial_drafts.sql")
    prefix.append(digest + "  20261014001700_017_editorial_media.sql")
    manifest = repo / m.MANIFEST
    manifest.write_text("\n".join(prefix) + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic private schema")
    sha = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(m, "BASELINE", sha)
    monkeypatch.setattr(m, "BLOB", git(repo, "rev-parse", f"HEAD:{m.PATH}"))
    monkeypatch.setattr(m, "MANIFEST_BLOB", git(repo, "rev-parse", f"HEAD:{m.MANIFEST}"))
    monkeypatch.setattr(m, "SQL_SHA256", digest)
    return repo, sha, sql


class Fake:
    ref = m.REF

    def __init__(self, *, prior=True, extra_bucket=False, mismatched_post=False):
        self.history = [(str(i), f"{i:03d}_earlier") for i in range(1, 16)]
        self.history.append(("16", m.PRIOR if prior else "016_wrong"))
        self.extra_bucket = extra_bucket
        self.mismatched_post = mismatched_post
        self.applied = []

    def project(self):
        pass

    def ledger(self):
        return list(self.history)

    def read(self, query):
        if not self.applied:
            return [{"id": "foreign"}] if self.extra_bucket and query == m.BUCKETS_SQL else []
        if query == m.BUCKETS_SQL:
            return [{
                "id": m.BUCKET, "name": m.BUCKET, "public": False,
                "file_size_limit": m.LIMIT,
                "allowed_mime_types": sorted(m.MIMES),
            }]
        if query == m.POLICIES_SQL:
            return [
                {"policyname": "classmo_editorial_insert", "cmd": "INSERT",
                 "roles": "{authenticated}", "qual": "",
                 "with_check": "classmo_private.editorial_storage_allowed(bucket_id,name,true)"},
                {"policyname": "classmo_editorial_read", "cmd": "SELECT",
                 "roles": "{authenticated}" if not self.mismatched_post else "{public}",
                 "qual": "classmo_private.editorial_storage_allowed(bucket_id,name,false)",
                 "with_check": ""},
            ]
        if query == m.TABLES_SQL:
            return [{"table_name": t, "rls": True, "force_rls": True}
                    for t in sorted(m.TABLES)]
        raise AssertionError("unreviewed read query")

    def apply(self, name, sql):
        assert name == m.NAME
        self.applied.append(sql)
        self.history.append(("17", name))


def test_exact_immutable_source_and_atomic_017_payload(tmp_path, monkeypatch):
    repo, sha, sql = source(tmp_path, monkeypatch)
    client = Fake()
    result = m.execute(client, repo, sha)
    assert client.applied == [m.BUCKET_DML + sql]
    assert result["result"] == "C017_PRIVATE_STORAGE_SCHEMA_APPLIED_RUNTIME_NOT_VERIFIED"
    assert result["previous_migrations"] == 16
    assert result["runtime_sql"] == result["independent_race"] == "NOT_RUN"
    assert result["upload_bytes_mime_exif"] == "NOT_RUN"
    assert result["public"] is False


@pytest.mark.parametrize("reason,mutate", [
    ("wrong_history", lambda f, repo, sha, m: Fake(prior=False)),
    ("occupied_bucket", lambda f, repo, sha, m: Fake(extra_bucket=True)),
])
def test_changed_provider_preflight_blocks_before_mutation(
    tmp_path, monkeypatch, reason, mutate,
):
    repo, sha, _sql = source(tmp_path, monkeypatch)
    client = mutate(None, repo, sha, m)
    with pytest.raises(m.Blocked):
        m.execute(client, repo, sha)
    assert client.applied == []


def test_moved_source_or_manifest_blocks_before_external_effect(tmp_path, monkeypatch):
    repo, sha, _sql = source(tmp_path, monkeypatch)
    client = Fake()
    with pytest.raises(m.Blocked):
        m.execute(client, repo, "f" * 40)
    assert client.applied == []
    manifest = repo / m.MANIFEST
    manifest.write_text(manifest.read_text().replace("20261014001700", "20261014001701"))
    with pytest.raises(m.Blocked):
        m.execute(client, repo, sha)
    assert client.applied == []


def test_post_storage_policy_broadening_never_yields_pass(tmp_path, monkeypatch):
    repo, sha, _sql = source(tmp_path, monkeypatch)
    client = Fake(mismatched_post=True)
    with pytest.raises(m.Blocked, match="C017_STORAGE_POLICY_BROAD"):
        m.execute(client, repo, sha)
    assert len(client.applied) == 1


def test_unauthorized_management_endpoints_and_payloads_are_denied():
    with pytest.raises(m.Blocked, match="C017_PROJECT_INVALID"):
        m.Client("a" * 20, "synthetic")
    with pytest.raises(m.Blocked, match="CREDENTIAL_MISSING"):
        m.Client(m.REF, "")
    client = m.Client(m.REF, "synthetic")
    with pytest.raises(m.Blocked, match="C017_READ_QUERY_DENIED"):
        client.read("select * from auth.users;")
    with pytest.raises(m.Blocked, match="REGISTERED_MIGRATION_API_DENIED"):
        client._request("/database/query", {"query": "delete from auth.users;"})
    with pytest.raises(m.Blocked, match="C017_MIGRATION_UNREVIEWED"):
        client.apply(m.NAME, "drop table public.editorial_media_slots;")


def test_read_only_http_403_never_logs_credential(monkeypatch):
    secret = "private-synthetic-secret"
    client = m.Client(m.REF, secret)

    def reject(req, timeout):
        assert timeout == 60
        assert req.get_method() == "POST"
        assert req.get_header("Authorization") == "Bearer " + secret
        raise HTTPError(req.full_url, 403, "private " + secret, {}, None)

    monkeypatch.setattr(m, "urlopen", reject)
    with pytest.raises(m.Blocked) as error:
        client.read(m.BUCKETS_SQL)
    assert str(error.value) == "C017_READ_HTTP_403"
    assert secret not in str(error.value)


def test_read_only_result_shape_and_verified_metadata(monkeypatch):
    client = m.Client(m.REF, "synthetic")
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return False

        def read(self):
            return json.dumps([{"id": m.BUCKET}]).encode()

    monkeypatch.setattr(m, "urlopen", lambda req, timeout: Response())
    assert client.read(m.BUCKETS_SQL) == [{"id": m.BUCKET}]
    with pytest.raises(m.Blocked):
        m.verify_bucket([{"id": m.BUCKET, "public": True}])


def test_workflow_scope_uses_existing_secret_and_exact_classmo_gate():
    workflow = (
        Path(__file__).resolve().parents[1] /
        ".github/workflows/provider-gate-approval-core.yml"
    ).read_text(encoding="utf-8")
    assert 'executor == "classmo_editorial_017_storage_apply"' in workflow
    assert 'gate == "editorial_017_storage_apply"' in workflow
    assert '"source_baseline_sha" => "4ea17a3d1d55298f1c86ad113b81c870a5d24363"' in workflow
    assert "scripts/classmo_editorial_017_storage_apply.py" in workflow
    assert "CLASSMO_SUPABASE_ACCESS_TOKEN:" in workflow
    assert "classmo-017-storage-schema.json" in workflow
