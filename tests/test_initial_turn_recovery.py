from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_direct_worker import git, registry

from wonjae_dispatcher_runner import direct_worker as worker
from wonjae_dispatcher_runner.initial_turn_recovery import (
    InitialTurnTimeout,
    run_initial_turns,
)
from wonjae_dispatcher_runner.repair_timeout import RepairPlanTimeout


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "product"
    repo.mkdir()
    git(repo, "init", "-b", "direct/ESC-1")
    git(repo, "config", "user.name", "Synthetic")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "source.txt").write_text("original\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo


@pytest.mark.parametrize("timeouts", [1, 2, 3])
def test_fresh_sdk_sessions_and_checkout_per_timeout(checkout, tmp_path, monkeypatch, timeouts):
    route = worker.load_direct_worker_route(registry(tmp_path), "ESC")
    sessions, turns, observations, events = [], [], [], []
    original_sha = worker.git_head(checkout)
    original_prompt = None
    monkeypatch.setattr(worker.os, "environ", dict(worker.os.environ))

    class FakeCodex:
        def __init__(self, **kwargs):
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def account(self, **kwargs):
            return SimpleNamespace(account=object())

        def thread_start(self, **kwargs):
            turns.append(object())
            return self

        def run(self, prompt, **kwargs):
            nonlocal original_prompt
            if original_prompt is None:
                original_prompt = prompt
            assert prompt == original_prompt  # Same SHA/project/command/task/source scope.
            assert (checkout / "source.txt").read_text() == "original\n"
            assert not (checkout / "partial.txt").exists()
            assert worker.git_head(checkout) == original_sha
            if len(turns) <= timeouts:
                (checkout / "partial.txt").write_text("rejected partial change")
                (checkout / "source.txt").write_text("rejected partial rewrite")
                raise RepairPlanTimeout("480-second deadline")
            (checkout / "source.txt").write_text("successful change\n")
            return SimpleNamespace(status="completed", error=None, final_response=json.dumps({
                "status": "CHANGED", "summary": "Synthetic success", "manual_action": "",
            }))

    monkeypatch.setattr(worker, "Codex", FakeCodex)

    def turn(attempt):
        try:
            result = worker.run_direct_worker(checkout, route, "next", tmp_path / "codex")
            assert result.status == "CHANGED"
            events.append("publish-eligible")
            return 0
        except worker.DirectWorkerTurnTimeout:
            return 75

    def guard():
        observations.append(worker.git_head(checkout))

    if timeouts == 3:
        with pytest.raises(InitialTurnTimeout, match="attempts=3 commits=0"):
            run_initial_turns(checkout, run_turn=turn, verify_authority=guard,
                              record=lambda *args: None)
        assert not events
    else:
        assert run_initial_turns(checkout, run_turn=turn, verify_authority=guard,
                                 record=lambda *args: None) == 0
        assert events == ["publish-eligible"]
        assert worker.changed_paths(checkout) == ("source.txt",)
    assert len(sessions) == min(timeouts + 1, 3)
    assert len({id(session) for session in sessions}) == len(sessions)
    assert all(session.closed for session in sessions)
    assert len({id(turn) for turn in turns}) == len(turns)
    assert set(observations) == {original_sha}
    assert git(checkout, "rev-list", "--count", "HEAD").strip() == "1"


@pytest.mark.parametrize("failure", ["authentication", "usage_limit", "unknown", "manual"])
def test_non_timeout_errors_never_replace_checkout(checkout, failure):
    attempts = []

    def turn(attempt):
        attempts.append(attempt)
        raise RuntimeError(failure)

    with pytest.raises(RuntimeError, match=failure):
        run_initial_turns(checkout, run_turn=turn, verify_authority=lambda: None,
                          record=lambda *args: None)
    assert attempts == [1]
    assert not list(checkout.parent.glob("product-abandoned-*"))


@pytest.mark.parametrize("mutation", ["protected", "history", "remote"])
def test_boundary_failure_prevents_timeout_replacement(checkout, mutation):
    attempts, guards = [], []

    def turn(attempt):
        attempts.append(attempt)
        if mutation == "protected":
            (checkout / "AGENTS.md").write_text("unauthorized")
        if mutation == "history":
            git(checkout, "config", "core.autocrlf", "false")
        return 75

    def guard():
        guards.append(1)
        if mutation == "remote" and len(guards) > 1:
            raise worker.DirectWorkerError("remote active HEAD changed")

    with pytest.raises(worker.DirectWorkerError):
        run_initial_turns(checkout, run_turn=turn, verify_authority=guard,
                          record=lambda *args: None)
    assert attempts == [1]
    assert not list(checkout.parent.glob("product-abandoned-*"))
