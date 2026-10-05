"""Local Git/Actions transport for executing the real workflow shell in tests."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STATE = Path(os.environ["SYNTHETIC_STATE"])


def load():
    return json.loads(STATE.read_text())


def save(value):
    STATE.write_text(json.dumps(value))


def git(*args):
    return subprocess.run(
        ["git", "-C", os.environ["PRODUCT_CHECKOUT"], *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Client:
    def __init__(self, *args):
        pass

    def get_run(self, number):
        return load()["run"]

    def get_issue(self, number):
        return load()["issue"]

    def get_pr(self, repository, number, **kwargs):
        state = load()
        actual = git("ls-remote", "origin", "refs/heads/" + state["pr"]["headRefName"]).split()[0]
        if actual != state["pr"]["headRefOid"]:
            if state["lag"] > 0:
                state["lag"] -= 1
            else:
                state["pr"]["headRefOid"] = actual
        state["pr_reads"].append(state["pr"]["headRefOid"])
        save(state)
        return state["pr"]

    def get_product_commit(self, repository, sha):
        return {
            "sha": sha,
            "parents": [
                {"sha": value} for value in git("rev-list", "--parents", "-n", "1", sha).split()[1:]
            ],
        }

    def add_comment(self, wake, body):
        state = load()
        number = max(c["id"] for c in state["issue"]["comments"]) + 1
        state["issue"]["comments"].append(
            {"id": number, "body": body, "user": {"login": "github-actions[bot]"}}
        )
        save(state)
        return number

    def update_comment(self, number, body):
        state = load()
        next(c for c in state["issue"]["comments"] if c["id"] == number)["body"] = body
        save(state)


def python(args):
    name = Path(args[0]).name
    if name == "actions_progress.py":
        return
    if name == "run_product_repair.py":
        from wonjae_dispatcher_runner.product_patch import (
            ProductEdit,
            apply_edit_plan,
            load_work_order,
        )

        state = load()
        state["repairs"] += 1
        path = Path(os.environ["PRODUCT_CHECKOUT"]) / "source.txt"
        apply_edit_plan(
            path.parent,
            (ProductEdit("source.txt", "write", path.read_text(), f"repair {state['repairs']}\n"),),
            load_work_order(Path(os.environ["WORK_ORDER_FILE"])),
            require_required_paths=False,
        )
        save(state)
        return
    if name == "source_terminal.py":
        from wonjae_dispatcher_runner import source_checkpoint as cp

        spec = importlib.util.spec_from_file_location("synthetic_checkpoint", args[0])
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        cli.Client = Client
        clock = [0]
        cp.time.monotonic = lambda: clock[0]
        cp.time.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        cli.checkpoint("--prepare-repair" in args)
        return
    if name == "validate_product_scope.py":
        from wonjae_dispatcher_runner.product_patch import load_work_order, validate_branch_scope

        validate_branch_scope(
            Path(os.environ["PRODUCT_CHECKOUT"]),
            load_work_order(Path(os.environ["WORK_ORDER_FILE"])),
        )
        return
    raise AssertionError(f"unexpected python operation: {name}")


def gh(args):
    state = load()
    if args[:2] in (["auth", "setup-git"], ["pr", "ready"]):
        return
    if args[:2] == ["secret", "set"]:
        sys.stdin.read()
        return
    if args[:2] == ["run", "watch"]:
        if "--exit-status" in args and state["runs"][args[2]]["conclusion"] != "success":
            raise SystemExit(1)
        return
    if args[:2] == ["run", "view"]:
        if "--log-failed" in args:
            print("Synthetic\tTypecheck\t2026-10-05T00:00:00.000Z Synthetic typecheck failure")
        else:
            run = state["runs"][args[2]]
            print(run["conclusion"], run["head"])
        return
    if args[0] == "api":
        url = next(value for value in args[1:] if value.startswith("repos/"))
        if "/actions/workflows/" in url:
            query = parse_qs(urlparse(url).query)
            sha, event = query["head_sha"][0], query["event"][0]
            if event == "pull_request":
                index = len(state["validation_heads"])
                state["validation_heads"].append(sha)
                conclusion = state["conclusions"][min(index, len(state["conclusions"]) - 1)]
            else:
                assert sha == state["integrated"]
                conclusion = state["integrated_conclusion"]
            run_id = str(len(state["runs"]) + 1)
            state["runs"][run_id] = {"head": sha, "conclusion": conclusion}
            save(state)
            print(run_id)
            return
        if url.endswith("/merge"):
            sha = next(value.removeprefix("sha=") for value in args if value.startswith("sha="))
            assert state["validation_heads"][-1] == sha
            assert list(state["runs"].values())[-1] == {"head": sha, "conclusion": "success"}
            assert state["pr"]["headRefOid"] == sha == git("rev-parse", "HEAD")
            integrated = git(
                "commit-tree",
                git("rev-parse", "HEAD^{tree}"),
                "-p",
                state["source"],
                "-p",
                sha,
                "-m",
                "synthetic integration",
            )
            git("push", "origin", integrated + ":refs/heads/cloud-dev-v1")
            state["merged_head"], state["integrated"] = sha, integrated
            save(state)
            print(json.dumps({"merged": True, "sha": integrated}))
            return
    raise AssertionError(f"unexpected gh operation: {args}")


if __name__ == "__main__":
    (gh if Path(sys.argv[0]).name == "gh" else python)(sys.argv[1:])
