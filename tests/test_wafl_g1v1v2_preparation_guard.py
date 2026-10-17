"""WAFL source-only G1-V1/V2 admission; no database or provider access."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/wafl_g1v1v2_preparation_guard.py"
spec = importlib.util.spec_from_file_location("wafl_g1_admission", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def source(tmp_path, monkeypatch):
    for p, body in (
        (mod.G1V1_PATH, {
            "schema_version": 1, "task": "WAFL-G1V-RPC-RLS-VERIFY-002",
            "remote_execution_authorized": False,
            "provider_contract": {"project_ref": mod.PROJECT_REF},
        }),
        (mod.G1V2_PATH, {"notValidConstraints": {
            "total": 50, "foreignKeys": 46, "checks": 4,
            "closed": 0, "namedInventorySupplied": False,
        }}),
    ):
        f = tmp_path / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(body))
    def git(_root, *args):
        if args == ("rev-parse", "HEAD"):
            return mod.SOURCE_BASELINE
        if args == ("merge-base", "--is-ancestor", mod.SOURCE_BASELINE, mod.SOURCE_BASELINE):
            return ""
        if args == ("rev-parse", f"{mod.SOURCE_BASELINE}:{mod.G1V1_PATH}"):
            return mod.G1V1_BLOB
        if args == ("rev-parse", f"{mod.SOURCE_BASELINE}:{mod.G1V2_PATH}"):
            return mod.G1V2_BLOB
        raise AssertionError("unexpected Git access")
    monkeypatch.setattr(mod, "git", git)
    return tmp_path


def test_exact_current_source_has_only_preparation_status(tmp_path, monkeypatch):
    repo = source(tmp_path, monkeypatch)
    receipt = mod.verify_source(repo, mod.SOURCE_BASELINE, mod.PROJECT_REF)
    assert receipt["result"].endswith("PREPARATION_ONLY_NOT_RUNTIME_ACCEPTANCE")
    assert receipt["provider_reads"] == 0
    assert receipt["provider_writes"] == 0
    assert receipt["g1v1_native_apple_two_subjects"] == "NOT_VERIFIED"
    assert receipt["g1v1_authenticated_rls_rpc_two_sessions"] == "NOT_RUN"
    assert receipt["g1v2_neon_constraints"] == "50_OPEN_46_FK_4_CHECK"
    assert receipt["g1_accept"] == "NOT_ACCEPTED"


def test_wrong_target_or_source_is_rejected_before_any_runtime(tmp_path, monkeypatch):
    repo = source(tmp_path, monkeypatch)
    with pytest.raises(mod.GuardBlocked, match="TARGET_NOT_REGISTERED"):
        mod.verify_source(repo, mod.SOURCE_BASELINE, "z" * 20)
    with pytest.raises(mod.GuardBlocked, match="SOURCE_SHA_INVALID"):
        mod.verify_source(repo, "short", mod.PROJECT_REF)
    with pytest.raises(mod.GuardBlocked, match="SOURCE_HEAD_CHANGED"):
        mod.verify_source(repo, "b" * 40, mod.PROJECT_REF)


@pytest.mark.parametrize("changed", [
    {"total": 49}, {"foreignKeys": 45}, {"checks": 5},
    {"closed": 1}, {"namedInventorySupplied": True},
])
def test_unreviewed_neon_constraints_block(tmp_path, monkeypatch, changed):
    repo = source(tmp_path, monkeypatch)
    path = repo / mod.G1V2_PATH
    manifest = json.loads(path.read_text())
    manifest["notValidConstraints"].update(changed)
    path.write_text(json.dumps(manifest))
    with pytest.raises(mod.GuardBlocked, match="BASELINE_CHANGED"):
        mod.verify_source(repo, mod.SOURCE_BASELINE, mod.PROJECT_REF)


def test_legacy_plan_cannot_claim_live_authorization(tmp_path, monkeypatch):
    repo = source(tmp_path, monkeypatch)
    path = repo / mod.G1V1_PATH
    plan = json.loads(path.read_text())
    plan["remote_execution_authorized"] = True
    path.write_text(json.dumps(plan))
    with pytest.raises(mod.GuardBlocked, match="PLAN_IDENTITY_MISMATCH"):
        mod.verify_source(repo, mod.SOURCE_BASELINE, mod.PROJECT_REF)


def test_workflow_guards_are_dormant_and_no_provider_call():
    core = (SCRIPT.parents[1] / ".github/workflows/provider-gate-approval-core.yml"
            ).read_text()
    assert "wafl_g1v1v2_preparation_guard_v1" in core
    assert "WAFL_G1_OWNER_RUNTIME_NOT_EXECUTABLE" in core
    script = SCRIPT.read_text()
    assert "api.supabase.com" not in script
    assert "psycopg" not in script
    assert "urlopen(" not in script
    assert "INSERT " not in script
    assert "NEON_DATABASE_URL" not in script
