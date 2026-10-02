from types import SimpleNamespace

import pytest

from wonjae_dispatcher_runner.codex_runner import (
    PilotError,
    extract_git_head_command_evidence,
)


SHA = "7a2688f14963de371c43bc67f729617b2d23de3c"


def command_item(
    repo_path,
    *,
    command: str = "git rev-parse HEAD",
    output: str = SHA,
    exit_code: int = 0,
    status: str = "completed",
):
    root = SimpleNamespace(
        type="commandExecution",
        command=command,
        cwd=str(repo_path),
        exit_code=exit_code,
        status=status,
        aggregated_output=output,
    )
    return SimpleNamespace(root=root)


def test_extract_command_evidence_accepts_exact_repo_sha(tmp_path) -> None:
    assert extract_git_head_command_evidence([command_item(tmp_path)], tmp_path) == SHA


def test_extract_command_evidence_accepts_shell_wrapped_git_command(tmp_path) -> None:
    item = command_item(tmp_path, command="/bin/bash -lc 'git rev-parse --verify HEAD'")
    assert extract_git_head_command_evidence([item], tmp_path) == SHA


@pytest.mark.parametrize(
    "item_factory",
    [
        lambda path: command_item(path, command="git status"),
        lambda path: command_item(path, output="7a2688f"),
        lambda path: command_item(path, exit_code=1),
        lambda path: command_item(path, status="failed"),
        lambda path: command_item(path / "other"),
    ],
)
def test_extract_command_evidence_rejects_unproven_items(tmp_path, item_factory) -> None:
    with pytest.raises(PilotError):
        extract_git_head_command_evidence([item_factory(tmp_path)], tmp_path)


def test_extract_command_evidence_rejects_multiple_distinct_shas(tmp_path) -> None:
    second = "b" * 40
    with pytest.raises(PilotError):
        extract_git_head_command_evidence(
            [
                command_item(tmp_path, output=SHA),
                command_item(tmp_path, output=second),
            ],
            tmp_path,
        )
