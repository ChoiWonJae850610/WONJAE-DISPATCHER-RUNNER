"""New trusted branch-writer boundaries only; prior cross-OS evidence is reused."""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
import yaml

from wonjae_dispatcher_runner import product_git_fence as adapter
from wonjae_dispatcher_runner.registered_source_fence import GitHubEvidenceReader
from wonjae_dispatcher_runner.source_writer_fence import (
    PARTICIPANTS,
    FenceError,
    SharedFence,
    atomic_json,
    validate_evidence,
)
from wonjae_dispatcher_runner.writer_participation import (
    WRITERS,
    code_inventory,
    validate_installed_code,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def branch_fixture(tmp_path):
    root = tmp_path / "isolated"
    root.mkdir(mode=0o700)
    atomic_json(root / "backend.json", {"backend_id": "test", "evidence_scope": "fixture"})
    binding = {"project": "ONE", "repository": "fixture/one", "branch": "development",
               "remote_sha": "a" * 40, "common_policy_sha": "b" * 40,
               "product_policy_sha": "a" * 40, "backend_id": "test",
               "participant_revisions": dict.fromkeys(PARTICIPANTS, "c" * 40)}
    facts = {**binding, "observed_at": 1000, "product_gate": "SOURCE_READY",
             "runs_complete": True, "jobs_complete": True, "prs_complete": True,
             "runner_reconciled": True, "runs": [], "jobs": [], "open_prs": [],
             "participants": {p: {"revision": "c" * 40, "enforced": True}
                              for p in PARTICIPANTS}}
    clock = [1000]
    fence = SharedFence(root, binding, lambda: copy.deepcopy(facts), clock=lambda: clock[0])

    def acquire(role="document_merge"):
        return fence.acquire({"task_id": "ONE-" + secrets.token_hex(8), "route": role,
                              "request_id": secrets.token_hex(16), "expires_at": 1900,
                              "lease_seconds": 100})

    return fence, binding, facts, clock, acquire


def scope_for(fence, ticket, tmp_path):
    # Exercise the real adapter and shared store; fixture scope cannot be enrolled.
    scope = adapter.MutationFence(tmp_path, "ONE", "document_merge", "document_merge",
                                  "a" * 40)
    scope.fence, scope.ticket, scope.initialized = fence, ticket, True
    scope.session_file = tmp_path / "test-session.json"
    scope.configuration = {}
    return scope


def test_every_reviewed_writer_and_exact_code_is_required():
    inventory = code_inventory()
    config = {"installed_writer_ids": list(WRITERS),
              "writer_code_sha256": inventory["code_sha256"]}
    validate_installed_code(config)
    assert inventory["installed"] is False
    assert "all_participants_installed" not in config
    for missing in WRITERS:
        incomplete = {**config, "installed_writer_ids": [x for x in WRITERS if x != missing]}
        with pytest.raises(FenceError, match="SET_INCOMPLETE"):
            validate_installed_code(incomplete)
    changed = dict(config["writer_code_sha256"])
    changed["scripts/prepare_provider_successor.rb"] = "0" * 64
    with pytest.raises(FenceError, match="CODE_MISMATCH"):
        validate_installed_code({**config, "writer_code_sha256": changed})


@pytest.mark.parametrize("gate", ["PROVIDER_GATE", "MANUAL_QA"])
def test_metadata_grant_preserves_real_source_and_provider_gates(branch_fixture, gate):
    fence, _, facts, _, acquire = branch_fixture
    facts["product_gate"] = gate
    with pytest.raises(FenceError, match="PRODUCT_GATE_BLOCKED"):
        acquire("direct_worker")
    ticket = acquire("provider_state_update")
    fence.verify(ticket, "before_state")
    with pytest.raises(FenceError, match="NOT_SOURCE_AUTHORITY"):
        fence.verify(ticket, "before_source")
    fence.release(ticket, {"task_id": ticket["task_id"], "epoch": ticket["epoch"],
                           "child_terminal": True, "publication_reconciled": True})


def test_source_and_metadata_are_one_actual_shared_owner(branch_fixture):
    fence, _, _, _, acquire = branch_fixture
    barrier = Barrier(2)

    def attempt(role):
        barrier.wait(timeout=5)
        try:
            return acquire(role)
        except FenceError:
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(attempt, ["direct_worker", "reconciliation"]))
    assert sum(x is not None for x in outcomes) == 1
    state = json.loads(fence.file.read_text())
    assert state["holder"]["lease_id"] == next(x for x in outcomes if x)["lease_id"]


@pytest.mark.parametrize("failure", ["epoch", "lease", "remote", "pr", "unknown_job"])
def test_actual_git_callback_refused_after_authority_loss(branch_fixture, tmp_path, failure):
    fence, _, facts, clock, acquire = branch_fixture
    scope = scope_for(fence, acquire(), tmp_path)
    if failure == "epoch":
        scope.ticket["epoch"] += 1
    elif failure == "lease":
        clock[0] += 101
        facts["observed_at"] = clock[0]
    elif failure == "remote":
        facts["remote_sha"] = "f" * 40
    elif failure == "pr":
        facts["open_prs"] = [{"number": 98, "base": "development", "head": "f" * 40}]
    else:
        facts["jobs"] = [{"status": "completed_maybe", "project": "ONE"}]
    called = []
    with pytest.raises(FenceError):
        scope.run("before_push", lambda: called.append("MUST_NOT_PUBLISH"))
    assert called == []
    assert json.loads(fence.file.read_text())["holder"]


def test_ambiguous_publication_stays_held_across_restart(branch_fixture, tmp_path):
    fence, binding, facts, _, acquire = branch_fixture
    scope = scope_for(fence, acquire(), tmp_path)

    def unknown():
        raise TimeoutError("synthetic lost receipt")

    with pytest.raises(TimeoutError):
        scope.run("before_pr", unknown)
    restarted = SharedFence(fence.directory.parent, binding, lambda: facts, clock=lambda: 1000)
    with pytest.raises(FenceError, match="QUARANTINED"):
        restarted.acquire({"task_id": "ONE-new", "route": "direct_worker",
                           "request_id": "e" * 32, "expires_at": 1900})
    with pytest.raises(FenceError):
        scope.finish()
    assert json.loads(fence.file.read_text())["holder"]["epoch"] == scope.ticket["epoch"]


def test_same_grant_epoch_exact_two_prs_and_no_third_pr_waiver(branch_fixture):
    fence, binding, facts, _, _ = branch_fixture
    prs = [{"number": n, "base": "development", "head": str(n) * 40, "task_id": "ONE-exact"}
           for n in (1, 2)]
    binding["approved_prs"] = prs
    fence.binding["approved_prs"] = prs
    ticket = fence.acquire({"task_id": "ONE-exact", "route": "source_recovery",
                            "request_id": "8" * 32, "expires_at": 1900})
    # Approved task binding is supplied by the trusted caller, not a hint.
    facts["open_prs"] = copy.deepcopy(prs)
    fence.verify(ticket, "before_push")
    assert json.loads(fence.file.read_text())["holder"]["epoch"] == ticket["epoch"]
    facts["open_prs"].append({"number": 3, "base": "development", "head": "3" * 40})
    with pytest.raises(FenceError, match="CONFLICTING_PR"):
        fence.verify(ticket, "before_merge")


def test_exact_merge_and_release_use_same_epoch(branch_fixture, tmp_path):
    fence, _, facts, _, acquire = branch_fixture
    scope = scope_for(fence, acquire(), tmp_path)
    scope._read = lambda endpoint: {"number": 9, "base": {"ref": "development"},
                                    "head": {"sha": "d" * 40}, "merged": True,
                                    "merge_commit_sha": "e" * 40}
    scope.observed_pr(9, "d" * 40)
    scope.point("before_merge")
    facts.update(remote_sha="e" * 40, product_gate="PROVIDER_GATE")
    scope.observed_merge(9, "e" * 40)
    scope.finish()
    state = json.loads(fence.file.read_text())
    assert state["holder"] is None
    assert state["consumed"][scope.ticket["task_id"]] == scope.ticket["epoch"]
    assert json.loads(scope.session_file.read_text())["released"] is True


def test_entry_requires_binding_and_does_not_mix_projects_or_roles(tmp_path):
    @adapter.writer_entry
    def unbound():
        adapter.guard_point("before_state")
    with pytest.raises(FenceError, match="CONTEXT_NOT_BOUND"):
        unbound()

    @adapter.writer_entry
    def wrong_nested():
        adapter.bind_writer(tmp_path, "ONE", "lean_ci_publish", "document_merge", "a" * 40)
        adapter.bind_writer(tmp_path, "TWO", "lean_ci_stage", "document_merge", "a" * 40)
    with pytest.raises(FenceError, match="SCOPE_CHANGED"):
        wrong_nested()


def test_multiple_active_jobs_cannot_all_impersonate_own_fenced_job(branch_fixture):
    _, binding, _, _, _ = branch_fixture
    own = {"repository": "fixture/runner", "run_id": 8, "attempt": 1, "job_id": 1}
    binding["run"] = own
    config = {"runner_repository": "fixture/runner", "common_repository": "fixture/control",
              "product_gate": "SOURCE_READY", "all_participants_installed": True,
              "participants": {p: {"revision": "c" * 40, "enforced": True}
                               for p in PARTICIPANTS},
              "workflow_rules": [{"repository": "fixture/runner", "path": "a.yml",
                                  "revision": "c" * 40, "fenced": True,
                                  "jobs": {"mine": "ONE", "other": "ONE"}}]}

    def read(endpoint):
        if endpoint.endswith("/branches/development"):
            return {"commit": {"sha": "a" * 40}}
        if endpoint.endswith("/branches/main"):
            return {"commit": {"sha": "b" * 40 if "control" in endpoint else "c" * 40}}
        if "/pulls?" in endpoint:
            return []
        if "/jobs?" in endpoint:
            return {"total_count": 2, "jobs": [
                {"id": i, "name": name, "status": "in_progress"}
                for i, name in [(1, "mine"), (2, "other")]]}
        runs = [{"id": 8, "run_attempt": 1, "status": "in_progress",
                 "path": "a.yml", "head_sha": "c" * 40}]
        return {"total_count": 1 if "runner/actions" in endpoint and
                "status=in_progress" in endpoint else 0,
                "workflow_runs": runs if "runner/actions" in endpoint and
                "status=in_progress" in endpoint else []}

    facts = GitHubEvidenceReader(binding, config, read)()
    assert not any(j["fenced_entry"] for j in facts["jobs"])
    with pytest.raises(FenceError, match="ACTIVE_OR_QUEUED_WRITER"):
        validate_evidence(binding, facts, facts["observed_at"])


def test_provider_commands_and_host_are_unchanged_and_git_is_a_separate_job():
    file = ROOT / ".github/workflows/provider-gate-approval-core.yml"
    workflow = yaml.safe_load(file.read_text())
    approve = workflow["jobs"]["approve"]
    assert approve["runs-on"] == "ubuntu-latest"
    payload = json.dumps(approve["steps"][:53], sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(payload.encode()).hexdigest() == (
        "d4d485265aabac48a57b3bb83768f083e4f0d4542cbee152828dc3ae46e5f268")
    publisher = workflow["jobs"]["publish-next-state"]
    assert publisher["needs"] == "approve"
    assert publisher["continue-on-error"] is True
    assert "SOURCE_WRITER_FENCE_CONFIG" in publisher["runs-on"]
    assert publisher["env"]["SOURCE_WRITER_FENCE_REGISTRY"].endswith("_fence_control/PROJECTS.yaml")


def test_all_actual_branch_writer_workflows_receive_registration():
    workflow_names = {f for _, files in WRITERS.values() for f in files if f.endswith("-core.yml")}
    workflow_names |= {".github/workflows/lean-ci-owner-stage.yml",
                       ".github/workflows/lean-ci-owner-publish.yml"}
    for file in workflow_names:
        workflow = yaml.safe_load((ROOT / file).read_text())
        assert any("SOURCE_WRITER_FENCE_CONFIG" in j.get("env", {})
                   for j in workflow["jobs"].values()), file
    for name in ("wafl-product-v2.yml", "esc-product-v2.yml", "muvel-product-v2.yml",
                 "classmo-product-pilot.yml"):
        source = (ROOT / ".github/workflows" / name).read_text()
        assert "--actor legacy_source" in source
        assert "--phase before_source" in source
        assert "--phase before_commit" in source and "--phase before_push" in source
        assert "--phase before_merge" in source
        assert 'if: ${{ success() }}' in source


def test_readonly_recovery_reconcile_is_not_a_branch_writer():
    source = (ROOT / "scripts/reconcile_recovery_docs.py").read_text()
    assert "read_only=true" in source
    assert '"--method", "PUT"' not in source
    assert '"--method", "POST"' not in source
    assert '.write_text(' not in source and '.write_bytes(' not in source
    assert '"--method", "PATCH"' not in source


def test_precise_publication_phases_and_finalizer_do_not_create_new_grants():
    root = "repos/fixture/one/"
    for suffix, phase in [("contents/state.yaml", "before_state"),
                          ("git/commits", "before_commit"),
                          ("git/refs/heads/dev", "before_push"),
                          ("pulls", "before_pr"), ("pulls/1/merge", "before_merge")]:
        assert adapter.publication_phase(root + suffix) == phase
    cli = (ROOT / "scripts/product_writer_guard.py").read_text()
    assert 'require_session=args.action != "before"' in cli
    workflow = (ROOT / ".github/workflows/reconciliation-finalizer-core.yml").read_text()
    assert 'if [ "$ALREADY_MERGED" != "true" ]; then' in workflow
