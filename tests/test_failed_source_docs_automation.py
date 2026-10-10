"""Read-only contract checks for proposed trusted automated recovery workflow."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from wonjae_dispatcher_runner.failure_recovery_document import RecoveryPreparationError

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/prepare_failed_source_recovery.py"
spec = importlib.util.spec_from_file_location("prepare_failed_source_recovery", SCRIPT)
assert spec and spec.loader
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def failed_run():
    return {
        "id": 37938489783,
        # GitHub REST actions/runs reflects run-name, not the workflow display label.
        "name": "Direct Worker WAFL next",
        "display_title": "Direct Worker WAFL next",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "path": ".github/workflows/direct-worker.yml@refs/heads/main",
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": "failure",
        "run_attempt": 1,
        "actor": {"login": "ChoiWonJae850610"},
        "repository": {"full_name": prep.RUNNER},
    }


def failed_jobs():
    return {"jobs": [{
        "id": 113846525951, "name": "wafl / direct-worker",
        "status": "completed", "conclusion": "failure", "runner_id": 31,
        "steps": [
            {"name": "Checkout trusted runner", "conclusion": "success"},
            {"name": "Run isolated Codex Direct Worker", "conclusion": "failure"},
        ],
    }]}


def registry():
    return {
        "schema_version": 1,
        "projects": {
            "WAFL": {
                "status": "active", "repository": "ChoiWonJae850610/WAFL",
                "branch": "cloud-dev-v1",
                "execution": {"mode": "direct_worker",
                              "state_path": ".wonjae/execution-state.yaml"},
            },
        },
    }


def test_owner_triggered_failed_run_and_exact_job_are_sufficient_to_inspect():
    prep.checked_run(failed_run(), "WAFL", "a" * 40)
    assert prep.checked_failure_job(failed_jobs(), "WAFL") == 113846525951
    assert prep.checked_registry(registry(), "WAFL")["branch"] == "cloud-dev-v1"


@pytest.mark.parametrize("product", ["WAFL", "CLASSMO", "ESC", "MUVEL"])
def test_actual_github_run_name_stays_bound_to_exact_product_next(product):
    run = failed_run()
    run["name"] = f"Direct Worker {product} next"
    run["display_title"] = f"Direct Worker {product} next"
    prep.checked_run(run, product, "a" * 40)
    run["name"] = f"Direct Worker {product} resume"
    with pytest.raises(RecoveryPreparationError):
        prep.checked_run(run, product, "a" * 40)


@pytest.mark.parametrize("field,value", [
    ("name", "Provider Gate Approval"),
    ("name", "Direct Worker"),
    ("name", "Direct Worker CLASSMO next"),
    ("name", "Direct Worker WAFL retry"),
    ("display_title", "Direct Worker CLASSMO next"),
    ("event", "pull_request"), ("head_branch", "cloud-dev-v1"),
    ("head_sha", "b" * 40), ("conclusion", "success"),
    ("status", "in_progress"), ("run_attempt", 2),
    ("repository", {"full_name": "ChoiWonJae850610/WAFL"}),
    ("actor", {"login": "someone-else"}),
])
def test_untrusted_or_stale_run_identity_is_rejected(field, value):
    run = failed_run()
    run[field] = value
    with pytest.raises(RecoveryPreparationError):
        prep.checked_run(run, "WAFL", "a" * 40)


@pytest.mark.parametrize("mutate", [
    lambda jobs: jobs["jobs"][0].update({"runner_id": 0}),
    lambda jobs: jobs["jobs"][0].update({"status": "in_progress"}),
    lambda jobs: jobs["jobs"][0]["steps"][1].update({"name": "EAS build"}),
    lambda jobs: jobs["jobs"][0]["steps"].append({
        "name": "Publish", "conclusion": "failure"}),
    lambda jobs: jobs["jobs"][0].update({"conclusion": "success"}),
    lambda jobs: jobs.update({"jobs": jobs["jobs"] * 21}),
])
def test_non_exact_failure_stage_fails_closed(mutate):
    jobs = failed_jobs()
    mutate(jobs)
    with pytest.raises(RecoveryPreparationError):
        prep.checked_failure_job(jobs, "WAFL")


@pytest.mark.parametrize("corrupt", [
    lambda r: r["projects"]["WAFL"].update({"repository": "example/WAFL"}),
    lambda r: r["projects"]["WAFL"].update({"status": "inactive"}),
    lambda r: r["projects"]["WAFL"].update({"branch": "main"}),
    lambda r: r["projects"]["WAFL"].update({"branch": "../another"}),
    lambda r: r["projects"]["WAFL"]["execution"].update({"mode": "dispatcher_v2"}),
    lambda r: r["projects"]["WAFL"]["execution"].update({"state_path": "/secrets"}),
])
def test_unknown_or_protected_product_registry_rejected(corrupt):
    value = registry()
    corrupt(value)
    with pytest.raises(RecoveryPreparationError):
        prep.checked_registry(value, "WAFL")


@pytest.mark.parametrize("alias", [
    "none", "state-directory", "state-file", "docs-directory",
    "operations-directory", "documentation-file",
])
def test_product_recovery_paths_reject_symlink_aliases(tmp_path, alias):
    root = tmp_path / "product"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "target.yaml").write_text("no mutation", encoding="utf-8")
    state_dir = root / ".wonjae"
    state_file = state_dir / "execution-state.yaml"
    docs_dir = root / "docs"
    operations_dir = docs_dir / "operations"
    doc = operations_dir / "RECOVERY-WAFL-CLOUD-RUNTIME-001-123.md"
    if alias == "state-directory":
        state_dir.symlink_to(outside, target_is_directory=True)
    else:
        state_dir.mkdir()
        if alias == "state-file":
            state_file.symlink_to(outside / "target.yaml")
        else:
            state_file.write_text("schema_version: 1", encoding="utf-8")
    if alias == "docs-directory":
        docs_dir.symlink_to(outside, target_is_directory=True)
    else:
        docs_dir.mkdir()
        if alias == "operations-directory":
            operations_dir.symlink_to(outside, target_is_directory=True)
        else:
            operations_dir.mkdir()
            if alias == "documentation-file":
                doc.symlink_to(outside / "target.yaml")
    if alias == "none":
        prep.checked_recovery_write_paths(root, state_file, doc)
    else:
        with pytest.raises(RecoveryPreparationError):
            prep.checked_recovery_write_paths(root, state_file, doc)
    assert (outside / "target.yaml").read_text(encoding="utf-8") == "no mutation"



def history_run(number: int, project: str = "CLASSMO"):
    return {"id": number, "display_title": f"Direct Worker {project} next"}


def mock_history_pages(monkeypatch, pages):
    calls = []

    def fake_github_api(endpoint, token, *, method="GET", fields=None):
        assert method == "GET" and fields is None and token == "read-only-token"
        assert "actions/workflows/direct-worker.yml/runs?event=workflow_dispatch" in endpoint
        assert "per_page=15" in endpoint and "per_page=100" not in endpoint
        page = int(endpoint.rsplit("&page=", 1)[1])
        calls.append(page)
        return {"workflow_runs": pages[page - 1]}

    monkeypatch.setattr(prep, "github_api", fake_github_api)
    return calls


def test_recent_source_fence_paginates_small_reads_and_finds_exact_original(monkeypatch):
    # Real GitHub metadata is roughly 15 KB per run: previous 100-run GET
    # exceeded the 500 KB fetch guard even with under 100 total run records.
    pages = [
        [history_run(number) for number in range(226, 211, -1)],
        [history_run(211, "WAFL")],
    ]
    calls = mock_history_pages(monkeypatch, pages)
    prep.checked_latest_owner_source_run("WAFL", 211, "read-only-token")
    assert calls == [1, 2]


def test_recent_source_fence_rejects_later_same_product_run(monkeypatch):
    calls = mock_history_pages(
        monkeypatch,
        [[history_run(222, "CLASSMO"), history_run(221, "WAFL"),
          history_run(220, "CLASSMO")]],
    )
    with pytest.raises(RecoveryPreparationError, match="supersedes"):
        prep.checked_latest_owner_source_run("WAFL", 220, "read-only-token")
    assert calls == [1]


@pytest.mark.parametrize("rows", [
    [history_run(212), history_run(214)],  # out of order
    [history_run(212), history_run(212)],  # duplicated ID
    [{"id": "212", "display_title": "Direct Worker CLASSMO next"}],
    [history_run(n) for n in range(220, 204, -1)],  # >15 records in one page
])
def test_recent_source_fence_fails_closed_on_malformed_or_oversized_page(
    monkeypatch, rows,
):
    mock_history_pages(monkeypatch, [rows])
    with pytest.raises(RecoveryPreparationError):
        prep.checked_latest_owner_source_run("WAFL", 205, "read-only-token")


def test_recent_source_fence_rejects_missing_original_even_with_no_later_wafl(
    monkeypatch,
):
    mock_history_pages(monkeypatch, [
        [history_run(215), history_run(214), history_run(211)],
    ])
    with pytest.raises(RecoveryPreparationError, match="absent"):
        prep.checked_latest_owner_source_run("WAFL", 212, "read-only-token")


def test_recent_source_fence_has_a_strict_total_history_bound(monkeypatch):
    pages = [
        [history_run(number) for number in range(300 - p * 15, 285 - p * 15, -1)]
        for p in range(7)
    ]
    calls = mock_history_pages(monkeypatch, pages)
    with pytest.raises(RecoveryPreparationError, match="absent"):
        prep.checked_latest_owner_source_run("WAFL", 150, "read-only-token")
    assert calls == list(range(1, 8))


@pytest.mark.parametrize("reason,expected", [
    ("unbounded GitHub metadata", "GITHUB_METADATA_BOUND"),
    ("later product source run supersedes this failure", "LATER_SOURCE_RUN"),
    ("private token=abc123", "UNCLASSIFIED"),
])
def test_recovery_rejection_codes_are_allowlisted_and_non_secret(
    monkeypatch, tmp_path, capsys, reason, expected,
):
    event = tmp_path / "event.json"
    registry = tmp_path / "registry.yaml"
    event.write_text("{}", encoding="utf-8")
    registry.write_text("{}", encoding="utf-8")

    def fake_plan(*_args):
        raise RecoveryPreparationError(reason)

    monkeypatch.setattr(prep, "plan", fake_plan)
    for name in ("RUNNER_READ_TOKEN", "PRODUCT_WRITE_TOKEN", "CONTROL_READ_TOKEN"):
        monkeypatch.setenv(name, "fixture-secret")
    monkeypatch.setattr(sys, "argv", [
        "recovery", "--project", "WAFL", "--event", str(event),
        "--registry", str(registry), "--workdir", str(tmp_path / "work"),
    ])
    assert prep.main() == 1
    output = capsys.readouterr().err
    assert "code=" + expected in output
    assert "fixture-secret" not in output
    assert "abc123" not in output


def test_recovery_workflow_is_not_a_new_source_or_merge_executor():
    top = yaml.safe_load((ROOT / ".github/workflows/automatic-source-recovery-docs.yml")
                         .read_text(encoding="utf-8"))
    trigger = top.get("on", top.get(True))
    assert set(trigger) == {"workflow_run"}
    assert trigger["workflow_run"]["workflows"] == ["Direct Worker"]
    assert trigger["workflow_run"]["types"] == ["completed"]
    assert top["permissions"] == {"contents": "read", "actions": "read"}
    assert set(top["jobs"]) == {"wafl", "classmo", "esc", "muvel"}
    for project in ("WAFL", "CLASSMO", "ESC", "MUVEL"):
        job = top["jobs"][project.lower()]
        assert job["with"]["project"] == project
        assert "workflow_run.conclusion == 'failure'" in job["if"]
        assert "workflow_run.event == 'workflow_dispatch'" in job["if"]
        assert job["secrets"]["product_write_token"] == (
            "${{ secrets." + project + "_WRITE_TOKEN }}"
        )
        assert job["uses"] == "./.github/workflows/automatic-source-recovery-docs-core.yml"
    core = yaml.safe_load(
        (ROOT / ".github/workflows/automatic-source-recovery-docs-core.yml")
        .read_text(encoding="utf-8")
    )
    assert core["permissions"] == {"contents": "read", "actions": "read"}
    assert core["jobs"]["prepare"]["runs-on"] == [
        "self-hosted", "Linux", "X64", "direct-worker",
    ]
    scripts = str(core)
    assert "prepare_failed_source_recovery.py" in scripts
    assert "dispatches" not in scripts and "eas build" not in scripts
    assert "pull_request_target" not in scripts
    assert "on:" in (ROOT / ".github/workflows/automatic-source-recovery-docs.yml"
                     ).read_text(encoding="utf-8")
