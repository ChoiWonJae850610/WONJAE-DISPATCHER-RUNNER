"""CLASSMO 016 G1-S exact Owner admission, migration and readback offline tests."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from urllib.error import HTTPError

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/classmo_editorial_016_schema_apply.py"
SPEC = importlib.util.spec_from_file_location("classmo_editorial_016_schema_apply", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def test_new_runner_endpoint_allowlist_and_errors_no_secrets(monkeypatch):
    client = m.ApplyClient("a" * 20, "synthetic-private-token")
    monkeypatch.setattr(
        m, "urlopen", lambda *_args, **_kwargs: pytest.fail("no network"))
    for path, payload in [
        ("/database/query", {"query": "select 1"}),
        ("/database/migrations", {"name": "015_old", "query": "select 1"}),
        ("/database/migrations", {"name": m.MIGRATION_NAME, "query": ""}),
        ("/database/query/read-only", {"query": "select dangerous()"}),
    ]:
        with pytest.raises(m.g1.Blocked, match="C016_UNREVIEWED_WRITE_OR_SQL"):
            client._post(path, payload)
    with pytest.raises(m.g1.Blocked, match="C016_POST_SQL_NOT_ALLOWED"):
        client.post_read("select pg_sleep(9)")


def test_403_schema_write_reports_only_safe_stage(monkeypatch):
    secret = "private-synthetic-token"
    client = m.ApplyClient("a" * 20, secret)

    def denied(request, timeout):
        assert timeout == 60
        assert request.get_method() == "POST"
        raise HTTPError(request.full_url, 403, "private " + secret, {}, None)

    monkeypatch.setattr(m, "urlopen", denied)
    with pytest.raises(m.g1.Blocked) as error:
        client.apply("create table t (id integer);")
    assert str(error.value) == "C016_PROVIDER_HTTP_403 stage=migration_apply"
    assert secret not in str(error.value)


def table_row():
    return {
        "name": "class_page_drafts", "owner": "postgres", "rls": True,
        "forced": True, "anon_select": False, "auth_select": False,
        "auth_insert": False,
    }


def rpc_rows():
    return [
        {
            "name": name, "owner": "postgres", "security_definer": True,
            "auth_exec": True, "anon_exec": False,
            "config": "{search_path=pg_catalog, public, classmo_private}",
        } for name in ("read_editorial_draft", "save_editorial_draft")
    ]


class Fake:
    def __init__(self):
        self.before = [{"name": f"{i:03d}_synthetic", "version": str(i)}
                       for i in range(1, 16)]
        self.applied = []
        self.after = None

    def read(self, sql):
        assert sql == m.g1.LEDGER
        return self.before if self.after is None else self.after

    def apply(self, sql):
        self.applied.append(sql)
        self.after = self.before + [{
            "name": m.MIGRATION_NAME, "version": "20261012001600",
        }]

    def post_read(self, sql):
        return [table_row()] if sql == m.POST_TABLE else rpc_rows()


def test_schema_g1s_exact_forward_only_apply_with_injected_readback(tmp_path, monkeypatch):
    source = tmp_path / m.g1.MIGRATION
    source.parent.mkdir(parents=True)
    contents = (
        "create table public.class_page_drafts (id integer);"
        "create function public.save_editorial_draft() returns void;"
    )
    source.write_text(contents)
    checksum = hashlib.sha256(contents.encode()).hexdigest()
    client = Fake()
    monkeypatch.setattr(
        m.g1, "execute",
        lambda *_args: {"ledger_001_015": "MATCHED"})
    monkeypatch.setattr(
        m.g1, "verify",
        lambda *_args: (["synthetic"] * 15, checksum))
    monkeypatch.setattr(m.g1, "verify_prior_ledger",
                        lambda *_args: None)
    result = m.execute(client, tmp_path, "b" * 40, "c" * 40)
    assert client.applied == [contents]
    assert result["result"] == "C016_SCHEMA_APPLIED_NOT_RUNTIME_ACCEPTED"
    assert result["migration_version"] == "20261012001600"
    assert result["psql_runtime"] == "NOT_RUN"
    assert result["synthetic_cleanup"] == "NOT_RUN"


@pytest.mark.parametrize("field,bad", [
    ("rls", False),
    ("forced", False),
    ("auth_insert", True),
    ("owner", "untrusted"),
])
def test_g1s_denies_unsafe_table_readback(field, bad):
    client = Fake()
    bad_table = table_row()
    bad_table[field] = bad
    client.post_read = lambda sql: ([bad_table] if sql == m.POST_TABLE else rpc_rows())
    with pytest.raises(m.g1.Blocked, match="C016_DRAFT_RLS_OR_GRANTS_UNVERIFIED"):
        m.verify_schema_readback(client)


def test_g1s_denies_unsafe_rpc_grants():
    client = Fake()
    rows = rpc_rows()
    rows[1]["anon_exec"] = True
    client.post_read = lambda sql: ([table_row()] if sql == m.POST_TABLE else rows)
    with pytest.raises(m.g1.Blocked, match="C016_RPC_GRANTS_UNVERIFIED"):
        m.verify_schema_readback(client)


def test_g1s_refuses_non_exact_baseline_without_ddl(tmp_path, monkeypatch):
    client = Fake()
    monkeypatch.setattr(m.g1, "execute",
                        lambda *_args: (_ for _ in ()).throw(
                            m.g1.Blocked("C016_LEDGER_NOT_READY")))
    with pytest.raises(m.g1.Blocked, match="C016_LEDGER_NOT_READY"):
        m.execute(client, tmp_path, "b" * 40, "c" * 40)
    assert client.applied == []


def test_workflow_registers_exact_isolated_g1s_handler():
    content = (
        __import__("pathlib").Path(__file__).resolve().parents[1] /
        ".github/workflows/provider-gate-approval-core.yml"
    ).read_text()
    assert 'executor == "classmo_editorial_016_schema_apply"' in content
    assert 'gate == "editorial_016_schema_apply"' in content
    assert "scripts/classmo_editorial_016_schema_apply.py" in content
    assert "CLASSMO_SUPABASE_ACCESS_TOKEN: " in content
    assert "classmo-016-schema-" in content
