"""Minimal offline tests for dormant CLASSMO and WAFL Owner LIVE routes."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_dormant_live_registration_pins_source_without_enabling_provider():
    workflow = (ROOT / ".github/workflows/provider-gate-approval-core.yml").read_text()
    assert workflow.count("source_adapter_blob") == 4
    assert workflow.count('config["source_adapter_blob"]') == 2
    assert workflow.count('pinned_path = expected.fetch("runtime_adapter_path")') == 2
    assert workflow.count('"rev-parse", "--verify"') == 2
    assert workflow.count("source pin mismatch") == 2
    assert 'config["ready_for_owner"] == false' in workflow
    assert 'abort("CLASSMO_P2_OWNER_RUNTIME_NOT_EXECUTABLE")' in workflow
    assert 'abort("WAFL_G1_OWNER_RUNTIME_NOT_EXECUTABLE")' in workflow
    # Live is still a separate registration, requiring a different reviewed blob.
    assert 'config["runtime_adapter_blob"].match?' in workflow



def source_only_fixture(file: str, sha: str, ref: str) -> dict:
    if file.startswith("wafl"):
        return {
            "sourceSha": sha, "projectRef": ref, "runtime": "NOT_VERIFIED",
            "neon": "NOT_VERIFIED", "G1_ACCEPT": False,
        }
    return {
        "schema_version": 1, "source_sha": sha, "project_ref": ref,
        "status": "SOURCE_ONLY_NOT_RUNTIME_ACCEPTANCE",
        "source_completion": "INCOMPLETE", "executable_live_adapter": False,
        "owner_gate": "REGISTERED_DEVELOPMENT_RUNTIME_HANDOFF_REQUIRED",
        "phases": dict.fromkeys(
            (
                "admission", "auth", "runtime_asset", "images", "http_matrix",
                "db_commit_race", "db_rollback_race", "cleanup",
            ),
            "NOT_RUN",
        ),
        "runtime_handoff": {
            "interface": "CLASSMO_P2_RUNTIME_HANDOFF_V1", "source_sha": sha,
            "project_ref": ref, "plan_blob": "2aebbaf9dfa2699c60d14e3e614927d6653103c1",
            "status": "NOT_VERIFIED", "live_admission_available": False,
        },
    }


@pytest.mark.parametrize(
    "file",
    ["wafl_g1_live_owner_handoff.py", "classmo_p2_live_owner_handoff.py"],
)
def test_source_preflight_uses_relative_tmp_and_never_forwards_credentials(
    tmp_path, monkeypatch, file
):
    module = load(file)
    sha = "b" * 40
    monkeypatch.setattr(module, "admit", lambda *_: tmp_path / "exact-product-adapter")
    monkeypatch.setenv("OWNER_PRIVATE_CANARY", "must-not-be-forwarded")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["cwd"] == tmp_path
        assert "OWNER_PRIVATE_CANARY" not in kwargs["env"]
        assert set(kwargs["env"]).issubset(
            {"PATH", "NODE_ENV", "PYTHONDONTWRITEBYTECODE"}
        )
        assert kwargs["timeout"] == 60
        path = argv[argv.index("--evidence") + 1]
        assert path.startswith("tmp/.") and path.endswith("/source.json")
        assert not Path(path).is_absolute()
        target = tmp_path / path
        assert target.parent.is_dir()
        target.write_text(
            json.dumps(source_only_fixture(file, sha, module.REF)), encoding="utf-8"
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.source_preflight(tmp_path, sha, module.REF, "c" * 40)
    assert result.endswith("SOURCE_CONTRACT_PASS_LIVE_NOT_RUN")
    assert len(calls) == 1
    assert not (tmp_path / "tmp").exists()  # Disposable source evidence only.


@pytest.mark.parametrize(
    "file",
    ["wafl_g1_live_owner_handoff.py", "classmo_p2_live_owner_handoff.py"],
)
def test_source_preflight_rejects_self_declared_live_success(tmp_path, monkeypatch, file):
    module = load(file)
    sha = "c" * 40
    monkeypatch.setattr(module, "admit", lambda *_: tmp_path / "exact-product-adapter")

    def forged_run(argv, **kwargs):
        payload = source_only_fixture(file, sha, module.REF)
        payload["status"] = "INDEPENDENTLY_VERIFIED"
        if file.startswith("wafl"):
            payload["runtime"] = "PASS"
            payload["G1_ACCEPT"] = True
        evidence = tmp_path / argv[argv.index("--evidence") + 1]
        evidence.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", forged_run)
    with pytest.raises(module.Blocked, match="SOURCE_RECEIPT_NOT_FAIL_CLOSED"):
        module.source_preflight(tmp_path, sha, module.REF, "d" * 40)
    assert not (tmp_path / "tmp").exists()


@pytest.mark.parametrize(
    "file",
    ["wafl_g1_live_owner_handoff.py", "classmo_p2_live_owner_handoff.py"],
)
def test_source_preflight_rejects_tmp_symlink(tmp_path, monkeypatch, file):
    module = load(file)
    monkeypatch.setattr(module, "admit", lambda *_: tmp_path / "exact-product-adapter")
    foreign = tmp_path.parent / f"foreign-{tmp_path.name}"
    foreign.mkdir()
    (tmp_path / "tmp").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(module.Blocked, match="SOURCE_PROBE_PATH_UNSAFE"):
        module.source_preflight(tmp_path, "a" * 40, module.REF, "b" * 40)


@pytest.mark.parametrize(
    ("file", "blocked"),
    [
        ("wafl_g1_live_owner_handoff.py", "TRUSTED_HOST_NOT_REGISTERED"),
        ("classmo_p2_live_owner_handoff.py", "TRUSTED_HOST_NOT_REGISTERED"),
    ],
)
def test_live_cannot_invoke_product_cli_without_registered_trust(
    tmp_path, monkeypatch, file, blocked
):
    module = load(file)
    monkeypatch.setattr(module, "admit", lambda *_: tmp_path / "exact-product-adapter")
    for key, value in {
        "GITHUB_ACTIONS": "true",
        "WONJAE_OWNER_LIVE_APPROVED": "yes",
        "WAFL_SUPABASE_ACCESS_TOKEN": "offline-only",
        "CLASSMO_SUPABASE_ACCESS_TOKEN": "offline-only",
        "CLASSMO_G1V_DEV_DB_PASSWORD": "offline-only",
    }.items():
        monkeypatch.setenv(key, value)

    def no_product_launch(*_args, **_kwargs):
        raise AssertionError("Unregistered LIVE route must not invoke product code")

    monkeypatch.setattr(module.subprocess, "run", no_product_launch)
    with pytest.raises(module.Blocked, match=blocked):
        module.execute(
            tmp_path, "a" * 40, module.REF, "b" * 40,
            tmp_path / "live-evidence.json", approved=True,
        )


def test_waffle_all_green_json_still_cannot_self_attest_live():
    module = load("wafl_g1_live_owner_handoff.py")
    sha = "a" * 40
    phases = {
        key: {"status": "PASS", "real_provider_readback": True}
        for key in (
            "apple_auth", "identity_map", "rls_rpc", "neon_50",
            "domain_parity", "cleanup",
        )
    }
    phases["apple_auth"]["subject_sha256"] = ["a" * 64, "b" * 64]
    phases["identity_map"]["audited_bijection"] = True
    phases["rls_rpc"]["independent_sessions"] = True
    phases["rls_rpc"]["cross_label_denied"] = True
    phases["neon_50"].update(
        count=50, fk=46, check=4, readback_per_invariant=True, bounded_violations=0
    )
    phases["domain_parity"]["fingerprints_equal"] = True
    phases["cleanup"]["independent_zero_residue"] = True
    with pytest.raises(module.Blocked, match="INDEPENDENT_READBACK_VERIFIER_NOT_REGISTERED"):
        module.verify_evidence(
            {
                "schema_version": 1, "source_sha": sha, "project_ref": module.REF,
                "status": "INDEPENDENTLY_VERIFIED", "phases": phases,
            },
            sha,
        )


@pytest.mark.parametrize(
    "file",
    ["wafl_g1_live_owner_handoff.py", "classmo_p2_live_owner_handoff.py"],
)
def test_source_probe_is_explicit_cli_mode_only(monkeypatch, capsys, tmp_path, file):
    module = load(file)
    monkeypatch.setattr(
        module, "source_preflight",
        lambda *_: "OFFLINE_SOURCE_CONTRACT_ONLY_LIVE_NOT_RUN",
    )
    monkeypatch.setattr(
        sys, "argv",
        [file, "--repo", str(tmp_path), "--source-sha", "a" * 40,
         "--project-ref", module.REF, "--adapter-blob", "b" * 40,
         "--evidence", str(tmp_path / "unused.json"), "--source-preflight"],
    )
    assert module.main() == 0
    assert "OFFLINE_SOURCE_CONTRACT_ONLY_LIVE_NOT_RUN" in capsys.readouterr().out


def test_trusted_workflow_runs_non_live_probe_before_guarded_execute():
    workflow = (ROOT / ".github/workflows/provider-gate-approval-core.yml").read_text()
    for script in (
        "classmo_p2_live_owner_handoff.py",
        "wafl_g1_live_owner_handoff.py",
    ):
        prefix = "python scripts/" + script
        assert workflow.count(prefix) == 2
        first = workflow.index(prefix)
        second = workflow.index(prefix, first + 1)
        assert "--source-preflight" in workflow[first:second]
        assert "--execute" in workflow[second:second + 400]
    assert workflow.count("--source-preflight") == 2
    assert "CLASSMO_P2_OWNER_RUNTIME_NOT_EXECUTABLE" in workflow
    assert "WAFL_G1_OWNER_RUNTIME_NOT_EXECUTABLE" in workflow



@pytest.mark.parametrize(
    "file",
    ["wafl_g1_live_owner_handoff.py", "classmo_p2_live_owner_handoff.py"],
)
def test_exact_live_host_blob_must_be_new_trusted_product_source(tmp_path, monkeypatch, file):
    module = load(file)
    sha, blob = "a" * 40, "b" * 40
    with pytest.raises(module.Blocked, match="TRUSTED_HOST_NOT_REGISTERED"):
        module.admit_host(tmp_path, sha, "")
    with pytest.raises(module.Blocked, match="TRUSTED_HOST_NOT_INTEGRATED"):
        module.admit_host(tmp_path, sha, blob)
    host = tmp_path / module.HOST
    host.parent.mkdir(parents=True)
    host.write_text("no provider fixture")
    monkeypatch.setattr(
        module, "git",
        lambda _repo, *args: sha if args[-1] == "HEAD" else blob,
    )
    assert module.admit_host(tmp_path, sha, blob) == host
    with pytest.raises(module.Blocked, match="TRUSTED_HOST_BLOB_MISMATCH"):
        module.admit_host(tmp_path, sha, "c" * 40)
    host.unlink()
    host.symlink_to(tmp_path / "untrusted-product-source")
    with pytest.raises(module.Blocked, match="TRUSTED_HOST_NOT_INTEGRATED"):
        module.admit_host(tmp_path, sha, blob)


@pytest.mark.parametrize(
    "file",
    ["wafl_g1_live_owner_handoff.py", "classmo_p2_live_owner_handoff.py"],
)
def test_trusted_host_launch_uses_exact_run_receipt_and_no_other_secrets(
    tmp_path, monkeypatch, file
):
    module = load(file)
    sha = "a" * 40
    evidence = tmp_path / "owner-proof.json"
    host = tmp_path / module.HOST
    host.parent.mkdir(parents=True)
    host.touch()
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("WONJAE_OWNER_LIVE_APPROVED", "yes")
    monkeypatch.setenv("EXTRA_PRODUCT_SECRET_CANARY", "must-not-leak")
    monkeypatch.setenv("WAFL_SUPABASE_ACCESS_TOKEN", "development-placeholder")
    monkeypatch.setenv("CLASSMO_SUPABASE_ACCESS_TOKEN", "development-placeholder")
    monkeypatch.setenv("CLASSMO_G1V_DEV_DB_PASSWORD", "development-placeholder")
    observed = []

    def fake_run(cmd, **kwargs):
        observed.append(cmd)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == 900
        assert "EXTRA_PRODUCT_SECRET_CANARY" not in kwargs["env"]
        assert "WAFL_SUPABASE_ACCESS_TOKEN" not in kwargs["env"] if file.startswith(
            "classmo"
        ) else "CLASSMO_SUPABASE_ACCESS_TOKEN" not in kwargs["env"]
        assert cmd[cmd.index("--source-sha") + 1] == sha
        assert cmd[cmd.index("--project-ref") + 1] == module.REF
        assert cmd[cmd.index("--run-id") + 1] == "12345"
        assert cmd[cmd.index("--evidence") + 1] == str(evidence)
        evidence.write_text(json.dumps({
            "source_sha": sha, "project_ref": module.REF,
            "owner_run_id": "12345", "status": "COLLECTED_REQUIRES_INDEPENDENT_REVIEW",
        }))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    receipt = module.launch_host(tmp_path, sha, module.REF, host, evidence)
    assert receipt["status"] == "COLLECTED_REQUIRES_INDEPENDENT_REVIEW"
    assert len(observed) == 1
    with pytest.raises(module.Blocked, match="LIVE_RECEIPT_PATH_UNSAFE"):
        module.launch_host(tmp_path, sha, module.REF, host, evidence)


@pytest.mark.parametrize(
    "file",
    ["wafl_g1_live_owner_handoff.py", "classmo_p2_live_owner_handoff.py"],
)
def test_registered_host_does_not_make_product_collected_result_live_pass(
    tmp_path, monkeypatch, file
):
    module = load(file)
    sha, blob = "a" * 40, "b" * 40
    monkeypatch.setattr(module, "admit", lambda *_: tmp_path / "source-only")
    monkeypatch.setattr(module, "admit_host", lambda *_: tmp_path / "trusted-live-host")
    monkeypatch.setattr(
        module, "launch_host",
        lambda *_: {"source_sha": sha, "project_ref": module.REF,
                    "owner_run_id": "12345",
                    "status": "COLLECTED_REQUIRES_INDEPENDENT_REVIEW"},
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("WONJAE_OWNER_LIVE_APPROVED", "yes")
    monkeypatch.setenv("WAFL_SUPABASE_ACCESS_TOKEN", "offline-only")
    monkeypatch.setenv("CLASSMO_SUPABASE_ACCESS_TOKEN", "offline-only")
    monkeypatch.setenv("CLASSMO_G1V_DEV_DB_PASSWORD", "offline-only")
    with pytest.raises(module.Blocked, match="LIVE_RECEIPT_UNVERIFIED"):
        module.execute(tmp_path, sha, module.REF, blob, tmp_path / "evidence.json",
                       approved=True, host_blob=blob)


def test_existing_provider_gate_requires_new_host_blob_only_for_future_live_mode():
    source = (ROOT / ".github/workflows/provider-gate-approval-core.yml").read_text()
    assert source.count("trusted_host_blob") == 8
    assert source.count('"PROVIDER_LIVE_HOST_BLOB" =>') == 2
    assert source.count('--host-blob "$PROVIDER_LIVE_HOST_BLOB" --execute') == 2
    assert source.count('if config["ready_for_owner"] == false') >= 2
    assert 'abort("CLASSMO_P2_OWNER_RUNTIME_NOT_EXECUTABLE")' in source
    assert 'abort("WAFL_G1_OWNER_RUNTIME_NOT_EXECUTABLE")' in source
