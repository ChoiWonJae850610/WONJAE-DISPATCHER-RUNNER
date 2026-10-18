"""Minimal offline tests for dormant CLASSMO and WAFL Owner LIVE routes."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(file: str):
    path = ROOT / "scripts" / file
    spec = importlib.util.spec_from_file_location(file[:-3], path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("file", "missing", "unpinned"),
    [
        ("classmo_p2_live_owner_handoff.py", "LIVE_ADAPTER_MISSING", "ADAPTER_BLOB_UNPINNED"),
        ("wafl_g1_live_owner_handoff.py", "LIVE_ADAPTER_MISSING", "LIVE_ADAPTER_UNPINNED"),
    ],
)
def test_exact_source_adapter_is_required(monkeypatch, tmp_path, file, missing, unpinned):
    module = load(file)
    monkeypatch.setattr(module, "verify_source", lambda *_: {"result": "SOURCE_ONLY"})
    with pytest.raises(module.Blocked, match="TARGET_UNREGISTERED"):
        module.admit(tmp_path, "a" * 40, "z" * 20, "b" * 40)
    with pytest.raises(module.Blocked, match=unpinned):
        module.admit(tmp_path, "a" * 40, module.REF, "not-a-blob")
    with pytest.raises(module.Blocked, match=missing):
        module.admit(tmp_path, "a" * 40, module.REF, "b" * 40)


@pytest.mark.parametrize(
    ("file", "marker"),
    [
        ("classmo_p2_live_owner_handoff.py", "RECEIPT_UNVERIFIED"),
        ("wafl_g1_live_owner_handoff.py", "RECEIPT_UNVERIFIED"),
    ],
)
def test_offline_receipts_cannot_earn_live_pass(file, marker):
    module = load(file)
    with pytest.raises(module.Blocked, match=marker):
        module.verify_evidence({
            "source_sha": "a" * 40, "project_ref": module.REF,
            "schema_version": 1, "status": "NOT_VERIFIED",
        }, "a" * 40)


def test_current_owner_gates_remain_non_executable():
    workflow = (ROOT / ".github/workflows/provider-gate-approval-core.yml").read_text()
    assert 'abort("CLASSMO_P2_OWNER_RUNTIME_NOT_EXECUTABLE")' in workflow
    assert 'abort("WAFL_G1_OWNER_RUNTIME_NOT_EXECUTABLE")' in workflow
    assert 'config["ready_for_owner"] == false' in workflow
    assert 'config["runtime_adapter_blob"].match?' in workflow
    assert '--adapter-blob "$PROVIDER_LIVE_ADAPTER_BLOB"' in workflow


def test_classmo_registered_source_contract_is_non_live_and_pinned():
    module = load("classmo_p2_live_owner_handoff.py")
    contract = module.source_runtime_contract()
    assert contract["contract"] == "CLASSMO_P2_RUNTIME_HANDOFF_V1"
    assert contract["stage"] == "SOURCE_INTERFACE_ONLY_PROVIDER_NOT_RUN"
    assert contract["gate"] == "editorial_027_p2_live_acceptance"
    assert contract["plan_git_blob"] == "2aebbaf9dfa2699c60d14e3e614927d6653103c1"
    assert contract["edge_runtime"] == "1.76.2"
    assert contract["wasm_version"] == "0.0.44"
    assert contract["live_approval"] is False
    assert contract["runtime_proof"] == contract["signature_proof"] == "NOT_VERIFIED"
    assert "cryptographically_verified_deployment_signature_from_reviewed_trust_root" in (
        contract["required_live_proof"]
    )
    with pytest.raises(module.Blocked, match="RUNTIME_SIGNATURE_VERIFIER_NOT_REGISTERED"):
        module.require_registered_runtime_signature({
            "status": "INDEPENDENTLY_VERIFIED",
            "signature": "PASS",
            "runtime": "1.76.2",
        })
