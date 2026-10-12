"""WAFL G1 isolated safety tests, without tokens or provider requests."""
from __future__ import annotations

import pytest

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
