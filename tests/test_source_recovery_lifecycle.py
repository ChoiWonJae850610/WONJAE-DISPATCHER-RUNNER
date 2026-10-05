"""Execute the production YAML's repair/merge/integrated gates against local Git."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_source_terminal import GitHub

from wonjae_dispatcher_runner.source_checkpoint import CHECKPOINT_HEADING
from wonjae_dispatcher_runner.source_pr_lifecycle import parse_fields

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    "wafl-product-v2.yml",
    "muvel-product-v2.yml",
    "esc-product-v2.yml",
    "classmo-product-pilot.yml",
)


def shell_step(workflow, name):
    lines = (ROOT / ".github/workflows" / workflow).read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if name in line and "- name:" in line)
    start = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |") + 1
    end = next(
        (
            i
            for i in range(start, len(lines))
            if lines[i].strip() and not lines[i].startswith("          ")
        ),
        len(lines),
    )
    return "\n".join(line[10:] for line in lines[start:end]).replace(
        "${{ github.token }}", "synthetic-runner-token"
    )


@pytest.mark.parametrize(
    "workflow,conclusions",
    [
        *[(workflow, ["failure", "failure", "success"]) for workflow in WORKFLOWS],
        (WORKFLOWS[2], ["success"]),
        (WORKFLOWS[2], ["failure", "success"]),
    ],
)
def test_actual_recovery_loop_waits_new_heads_then_merges_only_exact_pass(
    tmp_path,
    workflow,
    conclusions,
):
    run_lifecycle(tmp_path, workflow, conclusions)


@pytest.mark.parametrize("gate", ["repairs_exhausted", "integrated_failure"])
def test_actual_validation_failure_never_fabricates_completion(tmp_path, gate):
    run_lifecycle(
        tmp_path,
        WORKFLOWS[2],
        ["failure"] if gate == "repairs_exhausted" else ["failure", "success"],
        gate,
    )


def run_lifecycle(tmp_path, workflow, conclusions, gate=""):
    product, remote, binary = (tmp_path / name for name in ("product", "remote.git", "bin"))
    product.mkdir()
    binary.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(product), *args], check=True, text=True, capture_output=True
        ).stdout.strip()

    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    (product / "source.txt").write_text("base\n")
    git("add", ".")
    git("commit", "-qm", "source base")
    source = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(remote))
    git("push", "-q", "origin", "HEAD:refs/heads/cloud-dev-v1")
    github = GitHub()
    git("checkout", "-qb", github.pr["headRefName"])
    (product / "source.txt").write_text("initial task\n")
    git("commit", "-qam", "initial task")
    head = git("rev-parse", "HEAD")
    git("push", "-q", "origin", "HEAD:refs/heads/" + github.pr["headRefName"])
    old_source = github.record["source_base_sha"]
    github.record.update(
        schema_version=1,
        title="Synthetic repair",
        source_base_sha=source,
        integration_authorized=True,
        validation_workflow_path=".github/workflows/test.yml",
        required_reads=["source.txt"],
        allowed_paths=["source.txt"],
        required_changed_paths=["source.txt"],
        scope=["Synthetic"],
        exclusions=["No provider work"],
        completion_conditions=["Exact validation"],
    )
    github.run["display_title"] = github.run["display_title"].replace(old_source, source)
    github.pr["body"] = github.pr["body"].replace(old_source, source)
    github.pr["headRefOid"] = head
    for comment in github.issue["comments"]:
        comment["body"] = comment["body"].replace(old_source, source)
        if CHECKPOINT_HEADING in comment["body"]:
            comment["body"] = comment["body"].replace(github.binding["last_pr_head"], head)
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "run": github.run,
                "issue": github.issue,
                "pr": github.pr,
                "source": source,
                "lag": 2,
                "pr_reads": [],
                "repairs": 0,
                "runs": {},
                "validation_heads": [],
                "conclusions": conclusions,
                "integrated_conclusion": "failure" if gate else "success",
            }
        )
    )
    work = tmp_path / "work.json"
    work.write_text(json.dumps(github.record))
    stub = (ROOT / "tests/source_recovery_stub.py").read_text()
    for name in ("gh", "python"):
        path = binary / name
        path.write_text(f"#!{sys.executable}\n" + stub)
        path.chmod(0o755)
    codex = tmp_path / "synthetic-auth"
    codex.mkdir()
    (codex / "auth.json").write_text("{}")
    env_file = tmp_path / "github-env"
    env = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT / "tests"),
        "SYNTHETIC_STATE": str(state),
        "PRODUCT_CHECKOUT": str(product),
        "WORK_ORDER_FILE": str(work),
        "GITHUB_WORKSPACE": str(ROOT),
        "GITHUB_ENV": str(env_file),
        "RUNNER_TEMP": str(tmp_path),
        "CODEX_HOME": str(codex),
        "GH_TOKEN": "synthetic-product-token",
        "SECRET_ROTATION_TOKEN": "synthetic-rotation-token",
        "GITHUB_REPOSITORY": "owner/runner",
        "GITHUB_REPOSITORY_OWNER": "owner",
        "GITHUB_RUN_ID": "101",
        "ISSUE_NUMBER": "11",
        "PILOT_PR_NUMBER": "7",
        "PILOT_PR_URL": "https://example.invalid/pull/7",
        "PILOT_HEAD_SHA": head,
        "CONTROL_SHA": github.binding["control_sha"],
        "TASK_ID": github.record["task_id"],
        "PRODUCT_BRANCH": github.pr["headRefName"],
        "PRODUCT_REPOSITORY": "owner/CLASSMO",
        "VALIDATION_WORKFLOW_FILE": "test.yml",
    }
    phases = [
        "exact PR-head Actions with bounded recovery",
        "merge to active development branch",
        "exact integrated-SHA Actions",
    ]
    for phase in phases:
        if phase == phases[1]:
            # Run the genuine cumulative allowed/required gate, not a mock PASS.
            from wonjae_dispatcher_runner.product_patch import (
                load_work_order,
                validate_branch_scope,
            )

            validate_branch_scope(product, load_work_order(work))
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", shell_step(workflow, phase)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=90,
        )
        if gate == "repairs_exhausted" and phase == phases[0]:
            assert result.returncode != 0
            final = json.loads(state.read_text())
            assert final["repairs"] == 2 and "merged_head" not in final
            return
        if gate == "integrated_failure" and phase == phases[2]:
            assert result.returncode != 0
            return
        assert result.returncode == 0, result.stdout + result.stderr
        env.update(dict(line.split("=", 1) for line in env_file.read_text().splitlines()))
    final = json.loads(state.read_text())
    assert final["repairs"] == len(conclusions) - 1
    assert len(set(final["validation_heads"])) == len(conclusions)
    assert final["validation_heads"][-1] == final["merged_head"] == env["PILOT_HEAD_SHA"]
    comments = [c for c in final["issue"]["comments"] if CHECKPOINT_HEADING in c["body"]]
    assert len(comments) == 1
    assert parse_fields(comments[0]["body"])["last_pr_head"] == final["merged_head"]
    assert final["integrated"] == env["INTEGRATED_SHA"]
    assert list(final["runs"].values())[-1] == {
        "head": final["integrated"],
        "conclusion": "success",
    }
