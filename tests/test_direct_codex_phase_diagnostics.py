"""Synthetic, secret-free evidence for whole-turn SDK timeout classification."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_direct_worker import git, registry

from wonjae_dispatcher_runner import direct_worker as worker
from wonjae_dispatcher_runner.repair_timeout import RepairPlanTimeout


@pytest.fixture
def source(tmp_path, monkeypatch):
    repo = tmp_path / "product"
    repo.mkdir()
    git(repo, "init", "-b", "direct/ESC-synthetic")
    git(repo, "config", "user.name", "Synthetic")
    git(repo, "config", "user.email", "synthetic@example.invalid")
    (repo / "source.txt").write_text("unchanged\n", encoding="utf-8")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-m", "base")

    # Existing production code scrubs process env; keep the test's copy isolated.
    env = dict(worker.os.environ)
    env["PRODUCT_TOKEN"] = "synthetic-sensitive-material"
    monkeypatch.setattr(worker.os, "environ", env)
    route = worker.load_direct_worker_route(registry(tmp_path), "ESC")
    return repo, route


@pytest.mark.parametrize(
    "failure_phase",
    ["sdk_start", "account_lookup", "thread_start", "turn_run", "turn_result"],
)
def test_timeout_identifies_last_sdk_phase_without_logging_secrets(
    source, tmp_path, monkeypatch, capsys, failure_phase,
):
    repo, route = source

    class FakeCodex:
        def __init__(self, config):
            assert "synthetic-sensitive-material" not in json.dumps(config.env)

        def __enter__(self):
            if failure_phase == "sdk_start":
                raise RepairPlanTimeout("synthetic timeout")
            return self

        def __exit__(self, *_args):
            if failure_phase == "turn_result":
                raise RepairPlanTimeout("synthetic shutdown timeout")

        def account(self, **_kwargs):
            if failure_phase == "account_lookup":
                raise RepairPlanTimeout("synthetic timeout")
            return SimpleNamespace(account=object())

        def thread_start(self, **_kwargs):
            if failure_phase == "thread_start":
                raise RepairPlanTimeout("synthetic timeout")
            return self

        def run(self, _prompt, **_kwargs):
            if failure_phase == "turn_run":
                raise RepairPlanTimeout("synthetic timeout")
            return SimpleNamespace(
                status="completed", error=None,
                final_response=json.dumps({
                    "status": "NO_WORK",
                    "summary": "synthetic",
                    "manual_action": "",
                }),
            )

    monkeypatch.setattr(worker, "Codex", FakeCodex)
    with pytest.raises(worker.DirectWorkerTurnTimeout, match="timed out"):
        worker.run_direct_worker(repo, route, "next", tmp_path / "auth")

    output = capsys.readouterr().out
    assert f"DIRECT_CODEX_PHASE phase={failure_phase} elapsed_s=" in output
    assert f"DIRECT_CODEX_TIMEOUT_PHASE phase={failure_phase} elapsed_s=" in output
    assert "synthetic-sensitive-material" not in output
    assert "product-checkout" not in output
    assert worker.git_head(repo) == git(repo, "rev-parse", "HEAD").strip()
    assert worker.changed_paths(repo) == ()


def test_success_has_terminal_sdk_phase_and_no_timeout(source, tmp_path, monkeypatch, capsys):
    repo, route = source

    class FakeCodex:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def account(self, **_kwargs):
            return SimpleNamespace(account=object())

        def thread_start(self, **_kwargs):
            return self

        def run(self, _prompt, **_kwargs):
            (repo / "source.txt").write_text("updated\n", encoding="utf-8")
            return SimpleNamespace(
                status="completed", error=None,
                final_response=json.dumps({
                    "status": "CHANGED", "summary": "synthetic", "manual_action": "",
                }),
            )

    monkeypatch.setattr(worker, "Codex", FakeCodex)
    result = worker.run_direct_worker(repo, route, "next", tmp_path / "auth")
    output = capsys.readouterr().out
    assert result.status == "CHANGED"
    assert result.changed_paths == ("source.txt",)
    assert "DIRECT_CODEX_PHASE phase=sdk_closed elapsed_s=" in output
    assert "DIRECT_CODEX_TIMEOUT_PHASE" not in output
    assert "synthetic-sensitive-material" not in output


def test_non_timeout_failure_remains_distinct(source, tmp_path, monkeypatch, capsys):
    repo, route = source

    class FakeCodex:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def account(self, **_kwargs):
            raise RuntimeError("synthetic-account-denial")

    monkeypatch.setattr(worker, "Codex", FakeCodex)
    with pytest.raises(worker.CodexAccountLookupFailed, match="Codex account lookup failed"):
        worker.run_direct_worker(repo, route, "next", tmp_path / "auth")
    output = capsys.readouterr().out
    assert "DIRECT_CODEX_PHASE phase=account_lookup" in output
    assert "DIRECT_CODEX_FAILURE code=CODEX_AUTH_LOOKUP_ERROR phase=account_lookup" in output
    assert "synthetic-account-denial" not in output
    assert "DIRECT_CODEX_TIMEOUT_PHASE" not in output


def test_missing_account_after_restored_json_remains_blocked_with_fixed_marker(
    source, tmp_path, monkeypatch, capsys,
):
    repo, route = source

    class FakeCodex:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def account(self, **kwargs):
            assert kwargs == {"refresh_token": False}
            return SimpleNamespace(account=None)

    monkeypatch.setattr(worker, "Codex", FakeCodex)
    with pytest.raises(worker.CodexAccountSessionMissing, match="Codex account session is missing"):
        worker.run_direct_worker(repo, route, "resume", tmp_path / "auth")
    output = capsys.readouterr().out
    assert "DIRECT_CODEX_FAILURE code=CODEX_AUTH_ACCOUNT_UNAVAILABLE phase=account_lookup" in output
    assert "synthetic-sensitive-material" not in output
    assert worker.changed_paths(repo) == ()
