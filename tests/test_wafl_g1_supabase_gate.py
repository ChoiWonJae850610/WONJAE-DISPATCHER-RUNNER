"""WAFL G1 isolated safety tests, without tokens or provider requests."""
from __future__ import annotations

import pytest
from urllib.error import HTTPError

from scripts import wafl_g1_supabase_gate as g1


def test_g1_migrations_are_frozen_and_distinct():
    assert [name for name, _ in g1.MIGRATIONS] == [
        "wafl_cloud_auth_004", "wafl_cloud_domain_005", "wafl_cloud_rpc_006"
    ]
    assert len(g1.BASE_NAMES) == 4
    with pytest.raises(g1.GateBlocked, match="G1_PROJECT_REF_INVALID"):
        g1.Client("not-a-project", "synthetic-only")
    with pytest.raises(g1.GateBlocked, match="G1_CREDENTIAL_MISSING"):
        g1.Client("a" * 20, "")


def test_query_rejects_mutation_before_network(monkeypatch):
    api = g1.Client("a" * 20, "synthetic-only")
    requests = []
    monkeypatch.setattr(api, "api", lambda *args: requests.append(args))
    with pytest.raises(g1.GateBlocked, match="G1_READONLY_SQL_REQUIRED"):
        api.query("delete from public.users")
    assert requests == []


def test_apple_bridge_counts_remain_unverified_and_read_only():
    class ReadOnly:
        project = "a" * 20

        def __init__(self):
            self.steps = []

        def api(self, path):
            self.steps.append("GET")
            return {"id": self.project, "name": "WAFL Development"}

        def ledger(self):
            self.steps.append("LEDGER")
            return list(g1.BASE_NAMES)

        def exposure(self):
            self.steps.append("EXPOSURE")

        def tables(self):
            self.steps.append("TABLES")
            return {"companies": {"owner": "postgres", "rls": True, "forced": True}}

        def counts(self):
            self.steps.append("COUNTS")
            return {"users": 1, "apple": 1, "bridge": 0}

        def apply(self, name, sql):
            pytest.fail("read-only preflight cannot apply")

    client = ReadOnly()
    result = g1.preflight(client, {"companies"}, {"work_orders"})
    assert result["auth_counts"]["bridge"] == 0
    assert result["deferred_neon_constraints"] == 50
    assert result["apple_subject_equality"] == "NOT_VERIFIED"
    assert client.steps == ["GET", "LEDGER", "EXPOSURE", "TABLES", "COUNTS"]


def test_schema_apply_stops_without_independent_preflight_acceptance(monkeypatch):
    client = g1.Client("a" * 20, "synthetic-only")
    monkeypatch.setattr(g1, "verify_source", lambda *args: ({"companies"}, {"work_orders"}))
    monkeypatch.setattr(g1, "preflight", lambda *args: {"status": "OBSERVED_ONLY"})
    monkeypatch.setattr(client, "apply", lambda *args: pytest.fail("cannot apply"))
    with pytest.raises(g1.GateBlocked, match="G1_S_REQUIRES_INDEPENDENT_PREFLIGHT_APPROVAL"):
        g1.execute(client, None, "b" * 40, "b" * 40, ("c" * 40,) * 3,
                   "schema_apply", False)


def test_workflow_exact_source_owner_gate_and_credential_isolation():
    with open(".github/workflows/provider-gate-approval-core.yml", encoding="utf-8") as file:
        workflow = file.read()
    assert 'action["type"] == "PROVIDER_GATE"' in workflow
    assert 'action["gate"] == gate' in workflow
    assert 'config["ready_for_owner"] == true' in workflow
    assert 'config["preflight_accepted"] == true' in workflow
    assert '"PROVIDER_G1_MODE"' in workflow
    assert "scripts/wafl_g1_supabase_gate.py" in workflow
    assert "WAFL_SUPABASE_ACCESS_TOKEN: ${{ secrets.supabase_access_token }}" in workflow
    assert "Source-writing PR is open; provider execution refuses concurrent mutation" in workflow


def test_g1_readonly_uses_dedicated_read_only_sql_endpoint(monkeypatch):
    client = g1.Client("a" * 20, "synthetic-only")
    calls = []
    monkeypatch.setattr(client, "api",
                        lambda path, payload=None: calls.append((path, payload)) or [])
    assert client.query(g1.LEDGER_SQL) == []
    assert calls == [("/database/query/read-only", {"query": g1.LEDGER_SQL})]


@pytest.mark.parametrize("sql", [
    "select pg_sleep(1)",
    "select dangerous_mutating_function()",
    "select * from auth.users;",
    g1.LEDGER_SQL + " select 1;",
    "update public.users set id=id",
])
def test_g1_readonly_rejects_unreviewed_sql_before_network(monkeypatch, sql):
    client = g1.Client("a" * 20, "synthetic-only")
    monkeypatch.setattr(client, "api",
                        lambda *_args: pytest.fail("unreviewed SQL must not reach API"))
    with pytest.raises(g1.GateBlocked, match="G1_READONLY_SQL_REQUIRED"):
        client.query(sql)


def test_g1_readonly_cannot_address_mutating_or_ordinary_sql_endpoints(monkeypatch):
    client = g1.Client("a" * 20, "synthetic-only")
    monkeypatch.setattr(g1, "urlopen",
                        lambda *_args, **_kwargs: pytest.fail("no network calls"))
    for path in ("/database/query", "/database/migrations"):
        with pytest.raises(g1.GateBlocked, match="G1_ENDPOINT_NOT_ALLOWED"):
            client.api(path, {"query": "select 1"})
    with pytest.raises(g1.GateBlocked, match="G1_R_SCHEMA_AUTHORITY_PRESENT"):
        g1.Client("a" * 20, "synthetic-only", "read_only", True)


def test_g1_schema_apply_remains_locked_without_preflight_and_distinct_approval(
    monkeypatch,
):
    client = g1.Client("a" * 20, "synthetic-only", "schema_apply", True)
    monkeypatch.setattr(client, "api",
                        lambda *_args: pytest.fail("no preflight authority"))
    with pytest.raises(g1.GateBlocked, match="G1_SCHEMA_APPLY_NOT_AUTHORIZED"):
        client.apply(g1.MIGRATIONS[0][0], "select 1")
    # Even in a schema-configured client, metadata reads use the read-only role.
    mock = []
    monkeypatch.setattr(client, "api",
                        lambda path, payload=None: mock.append((path, payload)) or [])
    assert client.query(g1.LEDGER_SQL) == []
    assert mock == [("/database/query/read-only", {"query": g1.LEDGER_SQL})]


@pytest.mark.parametrize(("path", "payload", "stage"), [
    ("", None, "PROJECT_SETTINGS_READ"),
    ("/database/query/read-only", {"query": g1.LEDGER_SQL}, "DATABASE_READ"),
    ("/postgrest", None, "DATA_API_CONFIG_READ"),
])
@pytest.mark.parametrize("status", [401, 403])
def test_g1_http_access_denial_has_fixed_endpoint_class_only(
    monkeypatch, path, payload, stage, status,
):
    token = "synthetic-private-token"
    hidden = "untrusted-provider-error-with-private-information"

    def denied(request, timeout):
        assert timeout == 60
        assert request.headers["Authorization"] == "Bearer " + token
        raise HTTPError(request.full_url, status, hidden, {}, None)

    monkeypatch.setattr(g1, "urlopen", denied)
    client = g1.Client("a" * 20, token)
    with pytest.raises(g1.GateBlocked) as error:
        client.api(path, payload)
    assert str(error.value) == f"G1_PROVIDER_HTTP_{status}_{stage}"
    assert token not in str(error.value)
    assert hidden not in str(error.value)
    assert "a" * 20 not in str(error.value)


def test_g1_schema_apply_http_denial_is_labeled_but_not_retried(monkeypatch):
    calls = []

    def denied(request, timeout):
        calls.append(request.get_method())
        raise HTTPError(request.full_url, 403, "do not leak response", {}, None)

    monkeypatch.setattr(g1, "urlopen", denied)
    client = g1.Client("a" * 20, "synthetic-only", "schema_apply", True)
    client.schema_apply_ready = True
    with pytest.raises(g1.GateBlocked, match="G1_PROVIDER_HTTP_403_DATABASE_WRITE"):
        client.api("/database/migrations", {"name": g1.MIGRATIONS[0][0], "query": "select 1"})
    assert calls == ["POST"]


def test_g1_unrelated_provider_http_error_preserves_existing_code(monkeypatch):
    def unavailable(request, timeout):
        raise HTTPError(request.full_url, 500, "untrusted", {}, None)

    monkeypatch.setattr(g1, "urlopen", unavailable)
    client = g1.Client("a" * 20, "synthetic-only")
    with pytest.raises(g1.GateBlocked, match="^G1_PROVIDER_HTTP_500$"):
        client.api("")
