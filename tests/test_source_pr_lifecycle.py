from __future__ import annotations

from dataclasses import replace

from wonjae_dispatcher_runner.source_pr_lifecycle import (
    SourceIdentity,
    diagnose_open_job_prs,
    terminalize_exact_pr,
)

INCIDENT = {
    "project": "WAFL",
    "terminal_task": "WAFL-A83-044",
    "attempt": 1,
    "stale_product_pr": 12,
    "following_task": "WAFL-A83-045",
    "failed_runner_run": 37158436280,
    "failure": "Another WAFL job pull request is already open.",
    "conflict": "12 job/WAFL-A83-044",
}


class FakeGitHub:
    def __init__(self, prs, terminal_by_title=None):
        self.prs = {int(item["number"]): dict(item) for item in prs}
        self.terminal_by_title = terminal_by_title or {}
        self.closed = []

    def get_pr(self, repository, number):
        return dict(self.prs[number])

    def list_open_job_prs(self, repository, target_branch):
        return [
            dict(item)
            for item in self.prs.values()
            if item["state"] == "OPEN"
            and item["baseRefName"] == target_branch
            and item["headRefName"].startswith("job/")
        ]

    def close_pr(self, repository, number):
        self.closed.append(number)
        self.prs[number]["state"] = "CLOSED"

    def find_terminal_evidence(self, runner_repository, wake_title):
        return self.terminal_by_title.get(wake_title)


def identity(task="WAFL-A83-044", attempt=1, run_id="37156435069"):
    return SourceIdentity(
        project="WAFL",
        repository="ChoiWonJae850610/WAFL",
        target_branch="cloud-dev-v1",
        task_id=task,
        attempt=attempt,
        control_sha="52861dec42044a4434131779de489f0eb3a618e1",
        source_sha="c8311d808db48baf4e7e7b121bbfc3578f6d5fcd",
        runner_run_id=run_id,
    )


def pr_for(source, number=12, state="OPEN", merged_at=None):
    return {
        "number": number,
        "url": f"https://github.com/example/repo/pull/{number}",
        "state": state,
        "mergedAt": merged_at,
        "baseRefName": source.target_branch,
        "headRefName": source.expected_branch,
        "headRefOid": "3dae25efe8fc853ba10e4ccf384aa17f969c637f",
        "body": "\n".join(
            [
                "## Dispatcher v2 metadata",
                f"- task_id: `{source.task_id}`",
                f"- project: `{source.project}`",
                f"- attempt: `{source.attempt}`",
                "- revision: `1`",
                f"- repository: `{source.repository}`",
                f"- target_branch: `{source.target_branch}`",
                f"- source_base_sha: `{source.source_sha}`",
                f"- control_sha: `{source.control_sha}`",
            ]
        ),
    }


def evidence(source, result="FAILED", recovery="FINAL", pr_number=12):
    return "\n".join(
        [
            "## Dispatcher v2 terminal evidence",
            f"- project: `{source.project}`",
            f"- task_id: `{source.task_id}`",
            f"- attempt: `{source.attempt}`",
            f"- control_sha: `{source.control_sha}`",
            f"- source_sha: `{source.source_sha}`",
            f"- product_pr: `{pr_number}`",
            f"- runner_run_id: `{source.runner_run_id}`",
            "- runner_conclusion: `failure`",
            f"- result: `{result}`",
            f"- recovery_state: `{recovery}`",
        ]
    )


def test_a_failed_after_recovery_closes_exact_pr_unmerged() -> None:
    source = identity()
    fake = FakeGitHub([pr_for(source)])
    result = terminalize_exact_pr(fake, source, evidence(source), "FAILED", 12)
    assert result.status == "CLOSED"
    assert fake.prs[12]["state"] == "CLOSED"
    assert fake.prs[12]["mergedAt"] is None


def test_b_manual_required_closes_exact_pr_unmerged() -> None:
    source = identity()
    fake = FakeGitHub([pr_for(source)])
    result = terminalize_exact_pr(
        fake, source, evidence(source, "MANUAL_REQUIRED"), "MANUAL_REQUIRED", 12
    )
    assert result.status == "CLOSED"
    assert fake.closed == [12]
    assert fake.prs[12]["mergedAt"] is None


def test_c_cancelled_closes_exact_pr_unmerged() -> None:
    source = identity()
    fake = FakeGitHub([pr_for(source)])
    result = terminalize_exact_pr(
        fake, source, evidence(source, "CANCELLED", "FINAL_GUARD"), "CANCELLED", 12
    )
    assert result.status == "CLOSED"
    assert fake.closed == [12]


def test_d_completed_is_not_terminal_cleanup_authority() -> None:
    source = identity()
    fake = FakeGitHub([pr_for(source)])
    result = terminalize_exact_pr(
        fake, source, evidence(source, "COMPLETED"), "COMPLETED", 12
    )
    assert result.status == "RESIDUE"
    assert fake.closed == []


def test_e_other_task_pr_is_never_closed() -> None:
    source = identity()
    other = replace(
        source,
        task_id="WAFL-A83-999",
        control_sha="a" * 40,
    )
    fake = FakeGitHub([pr_for(other)])
    result = terminalize_exact_pr(fake, source, evidence(source), "FAILED", 12)
    assert result.status == "RESIDUE"
    assert fake.closed == []


def test_f_recovery_remaining_never_closes_pr() -> None:
    source = identity()
    fake = FakeGitHub([pr_for(source)])
    result = terminalize_exact_pr(
        fake, source, evidence(source, recovery="REPAIR_AVAILABLE"), "FAILED", 12
    )
    assert result.status == "RESIDUE"
    assert fake.closed == []


def test_g_terminal_guard_final_evidence_closes_discovered_exact_pr() -> None:
    source = identity()
    fake = FakeGitHub([pr_for(source)])
    result = terminalize_exact_pr(
        fake, source, evidence(source, "FAILED", "FINAL_GUARD"), "FAILED"
    )
    assert result.status == "CLOSED"
    assert result.pr_number == 12
    assert fake.closed == [12]


def test_h_next_task_has_no_concurrency_conflict_after_terminal_cleanup() -> None:
    source = identity()
    fake = FakeGitHub([pr_for(source)])
    cleanup = terminalize_exact_pr(fake, source, evidence(source), "FAILED", 12)
    assert cleanup.status == "CLOSED"
    conflicts = diagnose_open_job_prs(
        fake,
        "WAFL",
        "ChoiWonJae850610/WAFL",
        "cloud-dev-v1",
        "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER",
    )
    assert conflicts == []


def test_startup_diagnoses_but_does_not_ignore_stale_terminal_pr() -> None:
    source = identity()
    terminal = evidence(source)
    fake = FakeGitHub(
        [pr_for(source)],
        terminal_by_title={source.wake_title: (102, terminal)},
    )
    conflicts = diagnose_open_job_prs(
        fake,
        "WAFL",
        source.repository,
        source.target_branch,
        "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER",
    )
    assert len(conflicts) == 1
    assert conflicts[0].kind == "STALE_TERMINAL_OPEN_PR"
    assert conflicts[0].terminal_result == "FAILED"
    assert fake.closed == []


def test_incident_fixture_keeps_exact_regression_identity() -> None:
    assert INCIDENT == {
        "project": "WAFL",
        "terminal_task": "WAFL-A83-044",
        "attempt": 1,
        "stale_product_pr": 12,
        "following_task": "WAFL-A83-045",
        "failed_runner_run": 37158436280,
        "failure": "Another WAFL job pull request is already open.",
        "conflict": "12 job/WAFL-A83-044",
    }
