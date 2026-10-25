"""One-shot Owner-gated WAFL 013 Codex diagnostic; PR CI never runs main()."""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable

import yaml
from openai_codex import Codex, CodexConfig

from wonjae_dispatcher_runner.auth_store import restore_auth_json
from wonjae_dispatcher_runner.codex_safe_diagnostic import (
    MAX_PROBE_SECONDS,
    format_probe_result,
    probe_injected_sdk,
)

OWNER = "ChoiWonJae850610"
RUNNER = f"{OWNER}/WONJAE-DISPATCHER-RUNNER"
CONTROL = f"{OWNER}/DEV-CONTROL"
WAFL = f"{OWNER}/WAFL"
WORKFLOW = ".github/workflows/direct-worker-codex-diagnostic.yml"
FAILED_RUN = 40103585962
RECOVERY_PR = 120
RECOVERY_PR_HEAD = "5fc95c72e4f421749fb19dc724e0df4c1b558fec"
TASK = "WAFL-IMAGE-LOCAL-DERIVATIVES-013-001"
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_NAME = "Codex Diagnostic WAFL 013 "


class DiagnosticGateError(RuntimeError):
    """A fixed, non-sensitive guard code."""


def _document(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("encoding") != "base64":
        raise DiagnosticGateError("CONTENT_FORMAT")
    try:
        payload = base64.b64decode(value["content"])
        decoded = yaml.safe_load(payload.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DiagnosticGateError("CONTENT_FORMAT") from exc
    if not isinstance(decoded, dict):
        raise DiagnosticGateError("CONTENT_FORMAT")
    return decoded


def verify_owner_gate(
    read: Callable[[str], dict], *, source_sha: str, control_sha: str,
    runner_sha: str, run_id: int, actor: str,
) -> None:
    """Independent trusted-main admission, including once-only identity."""
    if (not SHA.fullmatch(source_sha) or not SHA.fullmatch(control_sha)
            or not SHA.fullmatch(runner_sha) or not isinstance(run_id, int)
            or run_id <= 0 or actor != OWNER):
        raise DiagnosticGateError("INPUT_IDENTITY")
    runner = read(f"repos/{RUNNER}/branches/main")
    control = read(f"repos/{CONTROL}/branches/main")
    wafl = read(f"repos/{WAFL}/branches/cloud-dev-v1")
    if runner.get("commit", {}).get("sha") != runner_sha:
        raise DiagnosticGateError("RUNNER_MAIN_CHANGED")
    if control.get("commit", {}).get("sha") != control_sha:
        raise DiagnosticGateError("CONTROL_MAIN_CHANGED")
    if wafl.get("commit", {}).get("sha") != source_sha:
        raise DiagnosticGateError("WAFL_HEAD_CHANGED")

    registry = _document(read(f"repos/{CONTROL}/contents/PROJECTS.yaml?ref={control_sha}"))
    route = registry.get("projects", {}).get("WAFL", {})
    if (route.get("status") != "active" or route.get("repository") != WAFL
            or route.get("branch") != "cloud-dev-v1"
            or route.get("execution", {}).get("mode") != "direct_worker"
            or route.get("execution", {}).get("runner_repository") != RUNNER
            or route.get("execution", {}).get("state_path") != ".wonjae/execution-state.yaml"):
        raise DiagnosticGateError("REGISTRY_ROUTE_CHANGED")
    state = _document(read(
        f"repos/{WAFL}/contents/.wonjae/execution-state.yaml?ref={source_sha}"
    ))
    action = state.get("next_action", {})
    if (action.get("type") != "SOURCE_READY" or action.get("source_task_id") != TASK
            or action.get("gate") is not None):
        raise DiagnosticGateError("PRODUCT_STAGE_CHANGED")

    failed = read(f"repos/{RUNNER}/actions/runs/{FAILED_RUN}")
    if (failed.get("id") != FAILED_RUN or failed.get("status") != "completed"
            or failed.get("conclusion") != "failure"
            or failed.get("event") != "workflow_dispatch"
            or failed.get("display_title") != "Direct Worker WAFL next"
            or failed.get("path", "").split("@")[0] != ".github/workflows/direct-worker.yml"):
        raise DiagnosticGateError("ORIGINAL_RUN_NOT_FAILED")
    recovery = read(f"repos/{WAFL}/pulls/{RECOVERY_PR}")
    if (recovery.get("number") != RECOVERY_PR
            or recovery.get("head", {}).get("sha") != RECOVERY_PR_HEAD
            or recovery.get("base", {}).get("ref") != "cloud-dev-v1"):
        raise DiagnosticGateError("RECOVERY_IDENTITY_CHANGED")

    prior = read(
        f"repos/{RUNNER}/actions/workflows/direct-worker-codex-diagnostic.yml/"
        "runs?event=workflow_dispatch&per_page=100"
    ).get("workflow_runs")
    if not isinstance(prior, list) or len(prior) >= 100:
        raise DiagnosticGateError("DIAGNOSTIC_HISTORY_UNAVAILABLE")
    if any(
        run.get("id") != run_id
        and run.get("display_title") == RUN_NAME + source_sha
        and run.get("head_branch") == "main"
        and run.get("path", "").split("@")[0] == WORKFLOW
        for run in prior
    ):
        raise DiagnosticGateError("ALREADY_ATTEMPTED")

    active = read(
        f"repos/{RUNNER}/actions/workflows/direct-worker.yml/"
        "runs?status=in_progress&per_page=100"
    ).get("workflow_runs")
    if not isinstance(active, list) or len(active) >= 100:
        raise DiagnosticGateError("SOURCE_RUN_INVENTORY_UNAVAILABLE")
    if any(run.get("display_title") == "Direct Worker WAFL next" for run in active):
        raise DiagnosticGateError("SOURCE_WORK_ACTIVE")


def _read_github(path: str, token: str) -> dict:
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "WONJAE-WAFL013-Owner-Diagnostic"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.load(response)
    except Exception as exc:
        raise DiagnosticGateError("GITHUB_READ_UNAVAILABLE") from exc
    if not isinstance(payload, dict):
        raise DiagnosticGateError("GITHUB_READ_FORMAT")
    return payload


def run_synthetic_probe(auth_json: str, directory: Path):
    work, auth, home = (directory / name for name in ("empty", "auth", "home"))
    work.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    restore_auth_json(auth, auth_json)
    safe_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home), "LANG": "C.UTF-8", "TZ": "UTC",
        "CODEX_HOME": str(auth), "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
    }
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        if os.environ.get(key):
            safe_env[key] = os.environ[key]
    # No GitHub token, serialized auth JSON or product/provider credential in SDK env.
    os.environ.clear()
    os.environ.update(safe_env)
    config = CodexConfig(cwd=str(work), env=safe_env)
    return probe_injected_sdk(
        sdk_factory=lambda: Codex(config=config),
        empty_workspace=work, deadline_seconds=MAX_PROBE_SECONDS,
    )


def main() -> int:
    if (os.environ.get("GITHUB_REPOSITORY") != RUNNER
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_WORKFLOW") != "WAFL 013 Codex Diagnostic"):
        print("SAFE_CODEX_OWNER_GATE status=DENIED code=TRIGGER")
        return 2
    try:
        run_id = int(os.environ.get("GITHUB_RUN_ID", ""))
        token = os.environ.get("OWNER_GITHUB_TOKEN", "")
        if not token:
            raise DiagnosticGateError("READ_TOKEN_UNAVAILABLE")
        verify_owner_gate(
            lambda path: _read_github(path, token),
            source_sha=os.environ.get("DIAGNOSTIC_SOURCE_SHA", ""),
            control_sha=os.environ.get("DIAGNOSTIC_CONTROL_SHA", ""),
            runner_sha=os.environ.get("GITHUB_SHA", ""), run_id=run_id,
            actor=os.environ.get("GITHUB_ACTOR", ""),
        )
    except (DiagnosticGateError, ValueError) as exc:
        code = exc.args[0] if isinstance(exc, DiagnosticGateError) else "RUN_ID_INVALID"
        print(f"SAFE_CODEX_OWNER_GATE status=DENIED code={code}")
        return 2
    auth_json = os.environ.get("CODEX_AUTH_JSON", "")
    if not auth_json:
        print("SAFE_CODEX_OWNER_GATE status=DENIED code=AUTH_SECRET_MISSING")
        return 2
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    parent = os.environ.get("RUNNER_TEMP", "")
    if not parent or not Path(parent).is_dir():
        print("SAFE_CODEX_DIAGNOSTIC status=RUNTIME_UNAVAILABLE")
        return 2
    print("SAFE_CODEX_OWNER_GATE status=PASS task=WAFL013")
    os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="wafl013-codex-probe-", dir=parent) as path:
            result = run_synthetic_probe(auth_json, Path(path))
    except Exception:
        print("SAFE_CODEX_DIAGNOSTIC status=RUNTIME_ERROR")
        return 2
    observed = format_probe_result(result)
    print(observed)
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(
                "### WAFL 013 — synthetic Codex stream diagnostic\n\n"
                + observed + "\n\n"
                "No product source/PR/provider; even model PASS is not WAFL 013 acceptance.\n"
            )
    return 0 if result.status in {"PASS", "PASS_NO_AGENT_OUTPUT"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
