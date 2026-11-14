from __future__ import annotations

import copy
import json
import secrets
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.registered_source_fence import (
    DirectWorkerFence,
    GitHubEvidenceReader,
    registered_configuration,
)
from wonjae_dispatcher_runner.source_writer_fence import (
    CONTRACT,
    PARTICIPANTS,
    FenceError,
    SharedFence,
    atomic_json,
)


@pytest.fixture
def example(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    atomic_json(root / "backend.json", {"backend_id": "fixture", "evidence_scope": "fixture"})
    binding = {"project": "ONE", "repository": "fixture/one", "branch": "development",
               "remote_sha": "a" * 40, "common_policy_sha": "b" * 40,
               "product_policy_sha": "c" * 40, "backend_id": "fixture",
               "participant_revisions": dict.fromkeys(PARTICIPANTS, "d" * 40),
               "readonly_workflow_blobs": ["e" * 40]}
    facts = {**binding, "observed_at": 1000, "product_gate": "SOURCE_READY",
             "runs_complete": True, "jobs_complete": True, "prs_complete": True,
             "runner_reconciled": True, "runs": [], "jobs": [], "open_prs": [],
             "participants": {p: {"revision": "d" * 40, "enforced": True} for p in PARTICIPANTS}}
    clock, reads = [1000], [0]

    def reader():
        reads[0] += 1
        return copy.deepcopy(facts)

    fence = SharedFence(root, binding, reader, clock=lambda: clock[0])

    def request():
        return {"route": "direct_worker", "task_id": "ONE-task-" + secrets.token_hex(4),
                "request_id": secrets.token_hex(16), "expires_at": clock[0] + 900}

    return root, binding, facts, clock, reads, fence, request


def test_atomic_restart_rejects_parallel_and_consumed_task(example):
    root, binding, facts, clock, _, fence, request = example
    original = request()
    ticket = fence.acquire(original)
    second = SharedFence(root, binding, lambda: facts, clock=lambda: clock[0])
    with pytest.raises(FenceError, match="WRITER_HELD"):
        second.acquire(request())
    fence.release(ticket, {"task_id": ticket["task_id"], "epoch": ticket["epoch"],
                           "child_terminal": True, "publication_reconciled": True})
    with pytest.raises(FenceError, match="ALREADY_CONSUMED"):
        second.acquire(original)


def test_local_records_do_not_poll_remote_but_source_and_publication_revalidate(example):
    _, _, _, _, reads, fence, request = example
    ticket = fence.acquire(request())
    assert reads[0] == 1
    proof = fence.verify(ticket, "local_record")
    assert proof["owned_grant_valid"] is True
    assert proof["complete_fresh_run_pr_inventory"] is False
    assert reads[0] == 1
    fence.verify(ticket, "before_source")
    fence.verify(ticket, "before_push")
    assert reads[0] == 3


@pytest.mark.parametrize("change", ["remote", "pr", "job", "unknown", "partial", "policy"])
def test_actual_trusted_publication_callback_never_runs_after_authority_changes(example, change):
    root, _, facts, _, _, fence, request = example
    adapter = DirectWorkerFence(fence, root / "ticket.json")
    adapter.acquire(request())
    if change == "remote":
        facts["remote_sha"] = "f" * 40
    elif change == "pr":
        facts["open_prs"] = [{"number": 1, "base": "development", "head": "f" * 40}]
    elif change in {"job", "unknown"}:
        facts["jobs"] = [{"project": "ONE", "scope_attested": True,
                          "status": "queued" if change == "job" else "not_known"}]
    elif change == "partial":
        facts["jobs_complete"] = False
    else:
        facts["common_policy_sha"] = "f" * 40
    called = []
    with pytest.raises(FenceError):
        adapter.publish("before_pr", lambda: called.append(True))
    assert called == []
    assert json.loads(fence.file.read_text())["quarantine"]


@pytest.mark.parametrize("participant", PARTICIPANTS)
def test_unfenced_mutating_entrypoint_blocks_even_when_idle(example, participant):
    _, _, facts, _, _, fence, request = example
    facts["participants"][participant]["enforced"] = False
    with pytest.raises(FenceError, match="UNFENCED_PARTICIPANT"):
        fence.acquire(request())


def test_exact_terminal_recovery_preserves_consumption_and_rejects_live_job(example):
    _, binding, facts, clock, _, fence, request = example
    run = {"repository": "fixture/runner", "run_id": 123, "attempt": 1, "job_id": 456}
    binding["run"] = run
    fence.binding["run"] = run
    ticket = fence.acquire(request())
    clock[0] += 61
    facts["observed_at"] = clock[0]
    with pytest.raises(FenceError, match="LEASE_EXPIRED"):
        fence.verify(ticket, "before_push")
    receipt = {"task_id": ticket["task_id"], "epoch": ticket["epoch"], "run": run,
               "run_jobs_terminal": True, "publication_reconciled": True}
    with pytest.raises(FenceError, match="EXACT_TERMINAL_RUN_READBACK"):
        fence.recover(ticket, receipt, lambda _: True)
    facts["terminal_run"] = {"run": run, "status": "completed", "all_jobs_terminal": True}
    facts["runs"] = [{"status": "in_progress", "project": "ONE", "run": run,
                      "scope_attested": True, "fenced_entry": True}]
    with pytest.raises(FenceError, match="RECOVERY_HAS_ACTIVE"):
        fence.recover(ticket, receipt, lambda _: True)
    facts["runs"] = []
    with pytest.raises(FenceError, match="EXACT_RUN_CHILD_PUBLICATION"):
        fence.recover(ticket, receipt, lambda _: False)
    fence.recover(ticket, receipt, lambda _: True)
    state = json.loads(fence.file.read_text())
    assert state["holder"] is None and state["epoch"] > ticket["epoch"]
    assert state["consumed"][ticket["task_id"]] == ticket["epoch"]


def test_only_exact_owned_pr_and_own_merge_can_release_after_remote_advance(example):
    _, _, facts, _, _, fence, request = example
    ticket = fence.acquire(request())
    own = {"number": 9, "base": "development", "head": "e" * 40,
           "task_id": ticket["task_id"]}
    fence.bind_pr(ticket, own)
    facts["open_prs"] = [own]
    fence.verify(ticket, "before_merge")
    receipt = {"repository": "fixture/one", "number": 9, "base": "development",
               "head": "e" * 40, "merged": True, "integrated_sha": "f" * 40}
    with pytest.raises(FenceError, match="EXACT_OWN_MERGE"):
        fence.acknowledge_integration(ticket, {**receipt, "number": 10})
    facts.update(remote_sha="f" * 40, open_prs=[], product_gate="PROVIDER_GATE")
    fence.acknowledge_integration(ticket, receipt)
    with pytest.raises(FenceError, match="INTEGRATED_TASK_IS_TERMINAL"):
        fence.verify(ticket, "before_source")
    # A rejected post-terminal mutation quarantines the owner, never transfers it.
    assert json.loads(fence.file.read_text())["holder"]["lease_id"] == ticket["lease_id"]


def test_clean_acknowledged_merge_release(example):
    _, _, facts, _, _, fence, request = example
    ticket = fence.acquire(request())
    own = {"number": 9, "base": "development", "head": "e" * 40,
           "task_id": ticket["task_id"]}
    fence.bind_pr(ticket, own)
    facts.update(remote_sha="f" * 40, open_prs=[], product_gate="MANUAL_QA")
    fence.acknowledge_integration(ticket, {
        "repository": "fixture/one", "number": 9, "base": "development",
        "head": "e" * 40, "merged": True, "integrated_sha": "f" * 40,
    })
    fence.release(ticket, {"task_id": ticket["task_id"], "epoch": ticket["epoch"],
                           "child_terminal": True, "publication_reconciled": True})
    assert json.loads(fence.file.read_text())["holder"] is None


def test_no_registration_means_no_new_authority_or_private_config_reads(tmp_path):
    file = tmp_path / "registry.yaml"
    file.write_text("projects:\n  ONE:\n    execution:\n      mode: direct_worker\n")
    assert registered_configuration(file, "ONE", {"SOURCE_WRITER_FENCE_CONFIG": "absent"}) is None


def test_fixture_or_unpinned_config_cannot_register_live_route(tmp_path):
    import hashlib
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"contract": CONTRACT, "all_participants_installed": True,
                                  "evidence_scope": "fixture"}))
    file = tmp_path / "registry.yaml"
    value = hashlib.sha256(config.read_bytes()).hexdigest()
    file.write_text("projects:\n  ONE:\n    execution:\n      mode: direct_worker\n"
                    "      source_writer_fence:\n        required: true\n"
                    f"        contract: {CONTRACT}\n        configuration_sha256: {value}\n")
    with pytest.raises(FenceError, match="FIXTURE_CONFIGURATION"):
        registered_configuration(file, "ONE", {"SOURCE_WRITER_FENCE_CONFIG": str(config)})
    config.write_text("{}")
    with pytest.raises(FenceError, match="CONFIGURATION_HASH_CHANGED"):
        registered_configuration(file, "ONE", {"SOURCE_WRITER_FENCE_CONFIG": str(config)})


def test_reader_rejects_truncated_pages_and_does_not_trust_display_titles(example):
    _, binding, _, _, _, _, _ = example
    config = {"runner_repository": "fixture/runner", "workflow_rules": []}
    calls = []

    def read(endpoint):
        calls.append(endpoint)
        return {"jobs": [], "total_count": 1}

    reader = GitHubEvidenceReader(binding, config, read)
    with pytest.raises(FenceError, match="INVENTORY_INCOMPLETE"):
        reader.collection("repos/fixture/runner/actions/runs/1/jobs", "jobs")
    assert len(calls) == 1
    assert "display_title" not in Path(__file__).parents[1].joinpath(
        "src/wonjae_dispatcher_runner/registered_source_fence.py",
    ).read_text().replace("display-title", "")


def test_workflow_hooks_surround_source_and_each_trusted_publication():
    import yaml
    root = Path(__file__).parents[1]
    source = (root / ".github/workflows/direct-worker-core.yml").read_text()
    document = yaml.safe_load(source)
    assert document["concurrency"] == {
        "group": "direct-worker-${{ inputs.project }}", "cancel-in-progress": False,
    }
    steps = document["jobs"]["direct-worker"]["steps"]
    indexes = {step["name"]: i for i, step in enumerate(steps)}
    assert indexes["Acquire registered cross-route source fence"] < indexes[
        "Run isolated Codex Direct Worker"
    ]
    for name, phase in [("Commit Direct Worker changes", "before_commit"),
                        ("Push Direct Worker branch", "before_push"),
                        ("Create or read Direct Worker PR", "before_pr"),
                        ("Merge exact validated Direct Worker PR", "before_merge")]:
        body = steps[indexes[name]]["run"]
        assert f"--phase {phase}" in body
    recovery = (root / "scripts/run_direct_worker_recovery.py").read_text()
    assert "fence.run_source(execute_source)" in recovery
    assert 'self-hosted","Linux","X64","direct-worker' in document[
        "jobs"
    ]["direct-worker"]["runs-on"]
