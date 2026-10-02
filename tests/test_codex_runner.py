from types import SimpleNamespace

import pytest

import wonjae_dispatcher_runner.codex_runner as runner


SHA = "7a2688f14963de371c43bc67f729617b2d23de3c"


def test_git_head_requires_full_sha(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner, "_run_git", lambda *_args: f"{SHA}\n")
    assert runner.git_head(tmp_path) == SHA


def test_git_status_returns_porcelain(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner, "_run_git", lambda *_args: "")
    assert runner.git_status(tmp_path) == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("completed", "completed"),
        (SimpleNamespace(value="completed"), "completed"),
    ],
)
def test_status_value_normalizes_sdk_enum_shape(value, expected) -> None:
    assert runner._status_value(value) == expected
