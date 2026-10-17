"""Offline CLASSMO 027-P2 admission tests; no Provider connections."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/classmo_editorial_027_p2_live_guard.py"
)
SPEC = importlib.util.spec_from_file_location("classmo_p2_guard", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    plan_path = tmp_path / mod.PLAN_PATH
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_ref": mod.PROJECT_REF,
                "environment": "development",
                "bucket": {"id": "classmo-editorial", "public": False},
                "execution_contract": {
                    "provider_status": "NOT_REGISTERED / NOT_RUN"
                },
                "normalization_source": {"live_status": "NOT_RUN"},
            }
        ),
        encoding="utf-8",
    )

    def fake_git(_repo: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return mod.SOURCE_BASELINE
        if args == (
            "merge-base", "--is-ancestor",
            mod.SOURCE_BASELINE, mod.SOURCE_BASELINE,
        ):
            return ""
        if args in (
            ("rev-parse", f"{mod.SOURCE_BASELINE}:{mod.PLAN_PATH}"),
            ("hash-object", mod.PLAN_PATH),
        ):
            return mod.PLAN_BLOB
        raise AssertionError("unexpected Git operation")

    monkeypatch.setattr(mod, "git", fake_git)
    return tmp_path


def test_source_admission_never_claims_live_acceptance(tmp_path, monkeypatch):
    repo = source(tmp_path, monkeypatch)
    result = mod.verify_source(repo, mod.SOURCE_BASELINE, mod.PROJECT_REF)
    assert result["result"].endswith("NOT_RUNTIME_ACCEPTANCE")
    assert result["provider_reads"] == result["provider_writes"] == 0
    assert result["p2_live_acceptance"] == "NOT_ACCEPTED"
    assert result["edge_deployment_and_wasm_bundle"] == "NOT_RUN"
    assert result["independent_postgres_race"] == "NOT_RUN"


@pytest.mark.parametrize("invalid", ["ref", "sha", "head"])
def test_wrong_project_or_source_stops(tmp_path, monkeypatch, invalid):
    repo = source(tmp_path, monkeypatch)
    if invalid == "ref":
        args = (mod.SOURCE_BASELINE, "z" * 20)
    elif invalid == "sha":
        args = ("not-a-sha", mod.PROJECT_REF)
    else:
        args = ("f" * 40, mod.PROJECT_REF)
    with pytest.raises(mod.GuardBlocked):
        mod.verify_source(repo, *args)


@pytest.mark.parametrize("field,value", [
    ("provider_status", "PASS"),
    ("live_status", "PASS"),
    ("project_ref", "other"),
])
def test_unreviewed_live_or_target_status_stops(
    tmp_path, monkeypatch, field, value
):
    repo = source(tmp_path, monkeypatch)
    plan_file = repo / mod.PLAN_PATH
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    if field == "provider_status":
        plan["execution_contract"][field] = value
    elif field == "live_status":
        plan["normalization_source"][field] = value
    else:
        plan[field] = value
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(mod.GuardBlocked):
        mod.verify_source(repo, mod.SOURCE_BASELINE, mod.PROJECT_REF)


def test_dormant_provider_gate_is_explicitly_fail_closed():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/provider-gate-approval-core.yml"
    ).read_text(encoding="utf-8")
    assert 'executor == "classmo_editorial_027_p2_live_guard_v1"' in workflow
    assert 'config["ready_for_owner"] == false' in workflow
    assert 'abort("CLASSMO_P2_OWNER_RUNTIME_NOT_EXECUTABLE")' in workflow
