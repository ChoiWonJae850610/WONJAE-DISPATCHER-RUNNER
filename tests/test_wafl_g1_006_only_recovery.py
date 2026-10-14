"""Synthetic/no-network tests for WAFL G1-S 006-only recovery."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from scripts import wafl_g1_006_only_recovery as m


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def synthetic_source(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    repo = tmp_path / "synthetic-wafl"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic")
    git(repo, "config", "user.email", "synthetic@invalid.test")
    migrations = m.g1.MIGRATIONS
    base_files = m.g1.BASE_FILES
    for index, file in enumerate(base_files):
        dest = repo / file
        dest.parent.mkdir(parents=True, exist_ok=True)
        names = [f"foundation_{n}" for n in range(index * 6, min(index * 6 + 6, 11))]
        dest.write_text("".join(f"CREATE TABLE public.{n} (id integer);\n" for n in names))
    for i, (_name, file) in enumerate(migrations):
        dest = repo / file
        dest.parent.mkdir(parents=True, exist_ok=True)
        if i == 0:
            content = "BEGIN;\n-- 004 synthetic-only\nCOMMIT;\n"
        elif i == 1:
            content = "".join(f"CREATE TABLE public.domain_{n} (id integer);\n"
                              for n in range(51))
        else:
            content = ("BEGIN;\n-- 006 synthetic-only\n"
                       "IF used_bytes + (CASE WHEN action = 'intent' THEN incoming_bytes ELSE 0 END) > quota_limit THEN\n"
                       "COMMIT;\n")
        dest.write_text(content)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic 004/005/006")
    sha = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(m, "BASELINE", sha)
    blobs = tuple(git(repo, "rev-parse", f"HEAD:{path}") for _n, path in migrations)
    monkeypatch.setattr(m, "BLOBS", blobs)
    return repo, sha


class Fake:
    def __init__(self, *, ledger_drift=False, exposed=("public",),
                 partial=False, grants=False, rls_drift=False,
                 post_policy_broad=False, post_ledger_missing=False):
        self.mode = "schema_apply"
        self.preflight_accepted = True
        self.schema_apply_ready = False
        self.exposed_schemas = ()
        self.expected = set(f"foundation_{i}" for i in range(11)) | set(
            f"domain_{i}" for i in range(51)
        )
        self.history = list(m.MIGRATIONS)
        if ledger_drift:
            self.history[-1] = ("20261013150814", "wrong_005")
        self.expose = exposed
        self.partial = partial
        self.grants = grants
        self.rls_drift = rls_drift
        self.post_policy_broad = post_policy_broad
        self.post_ledger_missing = post_ledger_missing
        self.applies = []
        self.applied_payload_hashes = []

    def api(self, path, payload=None):
        assert path == "" and payload is None
        return {"id": m.PROJECT, "name": m.PRODUCT}

    def exposure(self):
        if "wafl_private" in self.expose or "public" not in self.expose:
            raise m.Blocked("G1_DATA_API_EXPOSURE_UNVERIFIED")
        self.exposed_schemas = tuple(self.expose)

    def tables(self):
        return {
            name: {
                "rls": True, "forced": not (self.rls_drift and name == "domain_0"),
                "owner": "postgres",
            } for name in self.expected
        }

    def query(self, sql):
        if sql == m.g1.LEDGER_SQL:
            return [{"version": v, "name": n} for v, n in self.history]
        if sql == m.GRANTS_SQL:
            return [{"name": "domain_0"}] if self.grants else []
        if sql == m.SCHEMA_SQL:
            return [{"anon_usage": False, "auth_create": False}]
        if sql == m.PRIVATE_SQL:
            return [
                {"name": n, "owner": "postgres", "rls": True, "forced": True}
                for n in sorted(m.PRIVATE_TABLES)
            ] if self.applies or self.partial else []
        if sql == m.POLICIES_SQL:
            return [
                {"table_name": table, "name": policy,
                 "cmd": "SELECT",
                 "roles": "{PUBLIC}" if self.post_policy_broad else "{authenticated}",
                 "qual": "wafl_private.wafl_can_read_assigned_work_order(company_id, id)"}
                for table, policy in sorted(m.POLICIES)
            ] if self.applies or self.partial else []
        if sql == m.RPC_SQL:
            return [
                {"schema_name": schema, "name": name,
                 "owner": "postgres", "anon_execute": False,
                 "auth_execute": True, "definer": schema == "wafl_private"}
                for schema, name in sorted(m.RPC_PROFILES)
            ] if self.applies or self.partial else []
        raise AssertionError("Unexpected metadata read")

    def apply(self, name, sql):
        assert name == m.NAME and self.schema_apply_ready is True
        assert sql.startswith("BEGIN;")
        self.applies.append(sql)
        if not self.post_ledger_missing:
            self.history.append(("20261014170001", m.NAME))
        self.applied_payload_hashes.append({"migration": name, "synthetic": "ONLY"})


def test_exact_source_and_006_only_forward_apply(tmp_path, monkeypatch):
    repo, sha = synthetic_source(tmp_path, monkeypatch)
    client = Fake()
    result = m.execute(client, repo, sha)
    assert len(client.applies) == 1
    assert len(client.history) == 7
    assert result["result"] == "G1_006_ONLY_SCHEMA_APPLIED_NOT_G1_ACCEPT"
    assert result["installed_004_version"] == m.MIGRATIONS[4][0]
    assert result["installed_005_version"] == m.MIGRATIONS[5][0]
    assert result["public_relations"] == 62
    assert result["new_006_functions"] == 7
    assert result["new_assignee_policies"] == 2
    assert result["data_copy"] == result["r2_images"] == "NOT_RUN"


@pytest.mark.parametrize("argument", [
    {"ledger_drift": True}, {"exposed": ("public", "wafl_private")},
    {"exposed": ("public", "other")}, {"partial": True},
    {"grants": True}, {"rls_drift": True},
])
def test_preflight_refuses_wrong_ledger_grants_schema_or_006_partial(
    tmp_path, monkeypatch, argument,
):
    repo, sha = synthetic_source(tmp_path, monkeypatch)
    client = Fake(**argument)
    with pytest.raises(m.Blocked):
        m.execute(client, repo, sha)
    assert client.applies == []


def test_wrong_head_or_modified_migration_fails_without_write(tmp_path, monkeypatch):
    repo, sha = synthetic_source(tmp_path, monkeypatch)
    client = Fake()
    with pytest.raises(m.Blocked):
        m.execute(client, repo, "f" * 40)
    assert client.applies == []
    file = repo / m.MIGRATION_PATH
    file.write_text(file.read_text() + "\n-- unauthorized mutation\n")
    with pytest.raises(m.Blocked):
        m.execute(client, repo, sha)
    assert client.applies == []


def test_post_readback_failure_is_never_g1_accept(tmp_path, monkeypatch):
    repo, sha = synthetic_source(tmp_path, monkeypatch)
    for mode in ({"post_policy_broad": True}, {"post_ledger_missing": True}):
        fake = Fake(**mode)
        with pytest.raises(m.Blocked):
            m.execute(fake, repo, sha)
        assert len(fake.applies) == 1


def test_source_pins_only_existing_sql_files_and_expected_baseline():
    assert m.PROJECT == "aqqarddfmdfyzeonfgwl"
    assert m.BASELINE == "edbaba54a4c2d6eb924b610a2703e87c6484db67"
    assert m.BLOBS == (
        "09b5cf566f5b38726a68877afee4fed498b56b13",
        "572938ff0109a405918ee4ff20d5212ae09b55c1",
        "9cd1929f2893b94c4aea38ba286cabd19c464c3b",
    )
    assert m.NAME == "wafl_cloud_rpc_006"
    assert "wafl_cloud_domain_005" not in m.RPC_NAMES
    assert len(m.MIGRATIONS) == 6


def test_client_only_admits_006_and_requires_exact_source(monkeypatch):
    client = m.Client(m.PROJECT, "synthetic")
    with pytest.raises(m.Blocked, match="G1_006_ONLY_WRITE_DENIED"):
        client.apply("wafl_cloud_auth_004", "BEGIN;\nCOMMIT;")
    client.schema_apply_ready = True
    with pytest.raises(m.Blocked, match="G1_006_ONLY_WRITE_DENIED"):
        client.apply(m.NAME, "BEGIN;\nDROP SCHEMA public CASCADE;\nCOMMIT;")
    with pytest.raises(m.Blocked, match="G1_006_SQL_READ_DENIED"):
        client.query("select * from auth.users;")
    with pytest.raises(m.Blocked, match="G1_006_TARGET_INVALID"):
        m.Client("a" * 20, "synthetic")


def test_registration_and_workflow_are_distinct_from_old_004_005_apply():
    workflow = (
        Path(__file__).resolve().parents[1] /
        ".github/workflows/provider-gate-approval-core.yml"
    ).read_text(encoding="utf-8")
    assert 'executor == "wafl_supabase_g1_006_only_recovery"' in workflow
    assert 'gate == "g1_supabase_006_only_recovery"' in workflow
    assert '"migration_006_blob" => "9cd1929f2893b94c4aea38ba286cabd19c464c3b"' in workflow
    assert "scripts/wafl_g1_006_only_recovery.py" in workflow
    assert 'WAFL_SUPABASE_ACCESS_TOKEN: ' in workflow
    assert "wafl-g1-006-only.json" in workflow
    assert "g1_supabase_schema_apply" in workflow


def test_transaction_context_is_inherited_from_pinned_g1_helper():
    sql = "BEGIN;\n-- bounded stub\nCOMMIT;\n"
    payload = m.g1.approved_transaction_sql(m.NAME, sql, ("public",))
    assert "SET LOCAL pgrst.db_schemas = 'public';" in payload
    for stage in "abcd":
        assert "SET LOCAL wafl.rpc_006" + stage + "_apply_approved" in payload
    assert "SET LOCAL wafl.runtime_environment = 'development';" in payload
    with pytest.raises(m.Blocked):
        m.g1.approved_transaction_sql(m.NAME, sql, ("public", "wafl_private"))
    # Hashing a post-apply receipt does not constitute an actual provider write.
    assert hashlib.sha256(payload.encode()).hexdigest() != hashlib.sha256(sql.encode()).hexdigest()
