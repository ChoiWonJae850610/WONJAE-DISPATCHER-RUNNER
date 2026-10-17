"""Offline mocks for Owner-gated non-mutating CLASSMO/WAFL readiness."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/development_readiness_preflight.py"
spec = importlib.util.spec_from_file_location("development_readiness_preflight", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_only_fixed_project_sql_and_readonly_endpoint():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, _):
            return b"[]"

    calls = []

    def response(req, timeout):
        calls.append((req.full_url, req.get_method(), json.loads(req.data)))
        return Response()

    original = mod.urlopen
    try:
        mod.urlopen = response
        api = mod.ReadOnlySupabase("hfclkbqzxiqhcckwknih", "test")
        assert api.read(mod.LEDGER) == []
        assert calls[0][0].endswith("/database/query/read-only")
        assert calls[0][1] == "POST"
        assert calls[0][2] == {"query": mod.LEDGER}
        for path, sql in (
            ("/database/query", mod.LEDGER),
            ("/database/query/read-only", "select now();"),
            ("/database/migrations", mod.LEDGER),
        ):
            with pytest.raises(mod.ReadinessBlocked, match="NOT_ALLOWED"):
                api.request(path, sql)
        assert len(calls) == 1
    finally:
        mod.urlopen = original


def test_registered_development_route_requires_exact_plan_blob(tmp_path, monkeypatch):
    source = tmp_path / "repo"
    plan = source / mod.PROJECTS["CLASSMO"]["plan"]
    plan.parent.mkdir(parents=True)
    plan.write_text(json.dumps({"project_ref": mod.PROJECTS["CLASSMO"]["ref"]}))
    sha = "a" * 40
    blob = "b" * 40

    def git(_repo, *args):
        return sha if args == ("rev-parse", "HEAD") else blob

    monkeypatch.setattr(mod, "git_read", git)
    mod.local_contract(source, sha, "CLASSMO", blob)
    with pytest.raises(mod.ReadinessBlocked, match="PLAN_BLOB_MOVED"):
        mod.local_contract(source, sha, "CLASSMO", "c" * 40)
    plan.write_text('{"project_ref":"wrong"}')
    with pytest.raises(mod.ReadinessBlocked, match="PROJECT_MISMATCH"):
        mod.local_contract(source, sha, "CLASSMO", blob)


class FixtureClient:
    def __init__(self, project):
        self.project = project
        self.ref = mod.PROJECTS[project]["ref"]
        self.paths = []

    def request(self, path):
        self.paths.append(path)
        if path == "":
            return {"id": self.ref, "name": mod.PROJECTS[self.project]["name"]}
        if path == "/postgrest":
            return {"db_schema": "public"}
        raise AssertionError(path)

    def read(self, sql):
        self.paths.append(sql)
        if sql == mod.LEDGER:
            if self.project == "WAFL":
                return [{"name": name} for name in mod.WAFL_LEDGER]
            return [{"name": "016_editorial_drafts"}, {"name": "017_editorial_media"}]
        if sql == mod.CLASSMO_BUCKET:
            return [{
                "id": "classmo-editorial", "public": False,
                "file_size_limit": 5242880,
                "allowed_mime_types": ["image/png", "image/jpeg", "image/webp"],
            }]
        if sql == mod.WAFL_AUTH:
            return [{"users": 0, "apple": 0, "bridge": 0}]
        raise AssertionError(sql)


def test_classmo_readback_is_readiness_not_live_image_acceptance():
    client = FixtureClient("CLASSMO")
    result = mod.inspect_readiness("CLASSMO", client)
    assert result["result"] == "CLASSMO_P2_READONLY_PREFLIGHT_PASS_NOT_RUNTIME_ACCEPTANCE"
    assert result["authenticated_image_matrix"] == "NOT_RUN"
    assert result["two_session_db_race"] == "NOT_RUN"
    assert result["storage_cleanup"] == "NOT_RUN"
    assert result["provider_mutations"] == 0
    assert set(client.paths) == {"", "/postgrest", mod.LEDGER, mod.CLASSMO_BUCKET}

    class Bad(FixtureClient):
        def read(self, sql):
            if sql == mod.CLASSMO_BUCKET:
                return [{"id": "classmo-editorial", "public": True}]
            return super().read(sql)

    with pytest.raises(mod.ReadinessBlocked, match="PRIVATE_BUCKET_DRIFT"):
        mod.inspect_readiness("CLASSMO", Bad("CLASSMO"))


def test_wafl_seven_ledger_and_apple_subject_not_claimed():
    client = FixtureClient("WAFL")
    result = mod.inspect_readiness("WAFL", client)
    assert result["result"] == "WAFL_G1V_READONLY_PREFLIGHT_PASS_NOT_APPLE_ACCEPTANCE"
    assert result["apple_identity_count"] == 0
    assert result["two_distinct_apple_subjects"] == "NOT_VERIFIED"
    assert result["authenticated_rpc_rls"] == "NOT_RUN"
    assert result["neon_50_not_valid_parity"] == "NOT_RUN"
    assert result["provider_mutations"] == 0

    class SchemaDrift(FixtureClient):
        def request(self, path):
            if path == "/postgrest":
                return {"db_schemas": ["public", "wafl_private"]}
            return super().request(path)

    with pytest.raises(mod.ReadinessBlocked, match="PRIVATE_SCHEMA_EXPOSED"):
        mod.inspect_readiness("WAFL", SchemaDrift("WAFL"))


def test_exact_owner_flow_not_general_provider_or_write_token():
    core = (SCRIPT.parents[1] / ".github/workflows/provider-gate-approval-core.yml"
            ).read_text(encoding="utf-8")
    for item in (
        "registered_development_readiness_v1",
        "editorial_027_p2_readonly",
        "g1v_auth_db_readonly",
        "development_readiness_preflight.py",
        "source_baseline_sha",
        "plan_blob",
        "READINESS_SOURCE_BEFORE_ACCEPTED_STAGE",
    ):
        assert item in core
    assert "ready_for_owner\" => true" in core
    assert "secrets.supabase_access_token" in core
    assert "if: env.PROVIDER_EXECUTOR == 'registered_development_readiness_v1'" in core
    assert "new workflow_dispatch" not in core
    assert "CREATE ROLE" not in mod.LEDGER
    assert not any(word in "".join(mod.SQL).lower()
                   for word in (" insert ", " update ", " delete ", " drop ", " alter "))
