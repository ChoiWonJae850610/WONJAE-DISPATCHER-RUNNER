#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TASK_RE = re.compile(r"^[A-Z][A-Z0-9_-]*-[A-Z0-9][A-Z0-9-]*-[0-9]{3}$")
ALLOWED_ACTIONS = {"eas_workflow_update", "eas_update", "github_workflow_dispatch"}
ALLOWED_EAS_TARGETS = {
    "CLASSMO": {("preview", "preview")},
    "ESC": {("preview", "preview")},
    "WAFL": {("development", "alpha83-p5")},
}
WAFL_SAFE_APP_VARIANT_LINE = '      appVariant: development ? "development" : "production",'
FORBIDDEN_OTA_NAMES = {
    "app.json",
    "eas.json",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Podfile",
    "Podfile.lock",
    "settings.gradle",
    "build.gradle",
    "gradle.properties",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def safe_rel(value: str, label: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        fail(f"unsafe {label}")
    return value.replace("\\", "/")


def require_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        fail(f"{key} must be a non-empty string")
    return value.strip()


def write_env(path: str | Path, values: dict[str, str]) -> None:
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                fail(f"unsafe multiline environment value for {key}")
            handle.write(f"{key}={value}\n")


def validate_authority(record: dict[str, Any]) -> None:
    authority = record.get("provider_authority")
    if not isinstance(authority, dict):
        fail("provider_authority must be an object")
    required_false = (
        "production",
        "new_build",
        "credential_mutation",
        "device_mutation",
        "destructive",
    )
    for key in required_false:
        if authority.get(key) is not False:
            fail(f"provider_authority.{key} must be false for provider v2 pilot")


def prepare(args: argparse.Namespace) -> None:
    title_match = re.fullmatch(
        rf"\[PROVIDER-WAKE\]\[DISPATCHER-V2\] {re.escape(args.project)} "
        rf"({re.escape(args.project)}-[A-Z0-9][A-Z0-9-]*-[0-9]{{3}}) "
        r"([0-9a-f]{40}) ([0-9a-f]{40})",
        args.wake_title,
    )
    if title_match is None:
        fail("invalid provider wake title")
    task_id, control_sha, source_sha = title_match.groups()
    if control_sha != args.control_sha or source_sha != args.source_sha:
        fail("wake identity SHA mismatch")

    record = read_json(args.record)
    required = {
        "schema_version": 1,
        "task_id": task_id,
        "project": args.project,
        "repository": args.repository,
        "target_branch": args.target_branch,
        "source_base_sha": source_sha,
        "operation_type": "provider_action",
    }
    for key, expected in required.items():
        if record.get(key) != expected:
            fail(f"private provider record {key} mismatch")

    attempt = record.get("attempt")
    revision = record.get("revision")
    if not isinstance(attempt, int) or attempt < 1:
        fail("attempt must be a positive integer")
    if not isinstance(revision, int) or revision < 1:
        fail("revision must be a positive integer")
    profile = require_string(record, "profile")
    require_string(record, "title")
    validate_authority(record)

    action = record.get("provider_action")
    if not isinstance(action, dict):
        fail("provider_action must be an object")
    kind = require_string(action, "action")
    if kind not in ALLOWED_ACTIONS:
        fail("unsupported provider action")

    validation_path = require_string(record, "validation_workflow_path")
    if not validation_path.startswith(".github/workflows/"):
        fail("invalid validation workflow path")

    values = {
        "TASK_ID": task_id,
        "CONTROL_SHA": control_sha,
        "SOURCE_SHA": source_sha,
        "ATTEMPT": str(attempt),
        "REVISION": str(revision),
        "PROFILE": profile,
        "PROVIDER_ACTION": kind,
        "VALIDATION_WORKFLOW_FILE": Path(validation_path).name,
    }

    params = action.get("parameters")
    if not isinstance(params, dict):
        fail("provider_action.parameters must be an object")

    if kind in {"eas_workflow_update", "eas_update"}:
        working_directory = safe_rel(
            require_string(action, "working_directory"),
            "working_directory",
        )
        compatibility_base = require_string(action, "compatibility_base_sha")
        if not SHA_RE.fullmatch(compatibility_base):
            fail("compatibility_base_sha must be a commit SHA")
        runtime_version = require_string(action, "runtime_version")
        environment = require_string(params, "environment")
        channel = require_string(params, "channel")
        platform = require_string(params, "platform")
        message = require_string(params, "message")
        allowed_targets = ALLOWED_EAS_TARGETS.get(args.project, set())
        if (environment, channel) not in allowed_targets:
            fail("provider v2 EAS target is not allowlisted for project")
        if platform not in {"ios", "android"}:
            fail("unsupported EAS platform")
        values.update(
            {
                "PROVIDER_WORKING_DIRECTORY": working_directory,
                "PROVIDER_COMPATIBILITY_BASE_SHA": compatibility_base,
                "PROVIDER_RUNTIME_VERSION": runtime_version,
                "PROVIDER_ENVIRONMENT": environment,
                "PROVIDER_CHANNEL": channel,
                "PROVIDER_PLATFORM": platform,
                "PROVIDER_MESSAGE": message,
            }
        )
        if args.project == "WAFL":
            values["PROVIDER_APP_VARIANT"] = "development"
        if kind == "eas_workflow_update":
            workflow_file = safe_rel(require_string(action, "workflow_file"), "workflow_file")
            if not workflow_file.startswith(".eas/workflows/"):
                fail("EAS workflow must stay under .eas/workflows")
            values["PROVIDER_WORKFLOW_FILE"] = workflow_file
            inputs = action.get("inputs", {})
            if not isinstance(inputs, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in inputs.items()
            ):
                fail("provider_action.inputs must be a string mapping")
            values["PROVIDER_INPUTS_JSON"] = json.dumps(inputs, separators=(",", ":"))

    if kind == "github_workflow_dispatch":
        workflow_file = safe_rel(require_string(action, "workflow_file"), "workflow_file")
        if not workflow_file.startswith(".github/workflows/"):
            fail("GitHub provider workflow must stay under .github/workflows")
        inputs = action.get("inputs", {})
        if not isinstance(inputs, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in inputs.items()
        ):
            fail("provider_action.inputs must be a string mapping")
        values["PROVIDER_WORKFLOW_FILE"] = Path(workflow_file).name
        values["PROVIDER_INPUTS_JSON"] = json.dumps(inputs, separators=(",", ":"))

    write_env(args.github_env, values)


def git_output(repo: Path, *parts: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *parts],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _is_safe_wafl_app_variant_only_delta(
    repo: Path, base: str, head: str, path: str
) -> bool:
    diff = git_output(repo, "diff", "--unified=0", f"{base}..{head}", "--", path)
    added: list[str] = []
    removed: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return not removed and added == [WAFL_SAFE_APP_VARIANT_LINE]


def check_ota(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    base = args.base
    head = args.head
    if not SHA_RE.fullmatch(base) or not SHA_RE.fullmatch(head):
        fail("invalid OTA compatibility SHA")
    git_output(repo, "cat-file", "-e", f"{base}^{{commit}}")
    git_output(repo, "cat-file", "-e", f"{head}^{{commit}}")
    changed = git_output(repo, "diff", "--name-only", f"{base}..{head}")
    root = safe_rel(args.working_directory, "working_directory").rstrip("/") + "/"
    blocked: list[str] = []
    for raw in changed.splitlines():
        path = raw.replace("\\", "/")
        if not path.startswith(root):
            continue
        relative = path[len(root) :]
        name = Path(relative).name
        parts = Path(relative).parts
        if name in FORBIDDEN_OTA_NAMES:
            blocked.append(path)
            continue
        if parts and parts[0] in {"ios", "android", "plugins"}:
            blocked.append(path)
            continue
        if relative.startswith("app.config."):
            if (
                args.project == "WAFL"
                and path == "apps/mobile/app.config.js"
                and _is_safe_wafl_app_variant_only_delta(repo, base, head, path)
            ):
                continue
            blocked.append(path)
    if blocked:
        fail(
            "OTA compatibility gate blocked native/config/dependency changes: "
            + ", ".join(blocked)
        )


def dispatch_payload(args: argparse.Namespace) -> None:
    inputs = json.loads(args.inputs_json)
    payload = {"ref": args.ref, "inputs": inputs}
    Path(args.output).write_text(json.dumps(payload), encoding="utf-8")


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def parse_eas_run(args: argparse.Namespace) -> None:
    data = read_json(args.input)
    candidates = list(_walk(data))
    chosen = None
    for item in candidates:
        if any(key in item for key in ("workflowRunId", "workflow_run_id")):
            chosen = item
            break
    if chosen is None and isinstance(data, dict):
        chosen = data
    if not isinstance(chosen, dict):
        fail("could not parse EAS workflow result")
    run_id = chosen.get("workflowRunId") or chosen.get("workflow_run_id") or chosen.get("id")
    if not isinstance(run_id, str) or not run_id:
        fail("EAS workflow run ID missing")
    status = chosen.get("status") or chosen.get("state") or ""
    values = {"EAS_WORKFLOW_ID": run_id}
    if isinstance(status, str) and status:
        values["EAS_WORKFLOW_STATUS"] = status
    write_env(args.github_env, values)


def extract_provider_run(args: argparse.Namespace) -> None:
    data = read_json(args.input)
    comments = data if isinstance(data, list) else data.get("comments", [])
    if not isinstance(comments, list):
        fail("issue comments payload must be a list")
    for item in reversed(comments):
        if not isinstance(item, dict):
            continue
        body = item.get("body")
        if not isinstance(body, str) or "## Dispatcher v2 provider QUEUED evidence" not in body:
            continue
        match = re.search(r"- provider_run_id: \`([^\`]+)\`", body)
        if match:
            write_env(args.github_env, {"PROVIDER_RUN_ID": match.group(1)})
            return
    fail("provider queued run identity was not found on the issue")


def parse_eas_status(args: argparse.Namespace) -> None:
    data = read_json(args.input)
    known = {"NEW", "WAITING", "IN_PROGRESS", "SUCCESS", "FAILURE", "CANCELED", "ACTION_REQUIRED"}
    for item in _walk(data):
        status = item.get("status") or item.get("state")
        if isinstance(status, str):
            normalized = status.upper()
            if normalized in known:
                write_env(args.github_env, {"PROVIDER_STATUS": normalized})
                return
    fail("EAS workflow status was not found")


def _message_matches(candidate: Any, expected: str) -> bool:
    if candidate == expected:
        return True
    if not isinstance(candidate, str):
        return False
    return candidate.startswith(f'"{expected}" (')


def parse_update_group(args: argparse.Namespace) -> None:
    data = read_json(args.input)
    matches: list[dict[str, Any]] = []
    for item in _walk(data):
        group_id = item.get("group") or item.get("groupId") or item.get("group_id")
        item_runtime = item.get("runtimeVersion") or item.get("runtime_version")
        platforms = item.get("platforms")
        if not isinstance(group_id, str) or not group_id:
            continue
        if item_runtime != args.runtime_version:
            continue
        if not _message_matches(item.get("message"), args.message):
            continue
        if isinstance(platforms, str):
            platform_values = {value.strip().lower() for value in platforms.split(",")}
            if args.platform.lower() not in platform_values:
                continue
        matches.append(item)
    if not matches:
        fail("matching EAS update group was not found after provider execution")
    group_id = matches[0].get("group") or matches[0].get("groupId") or matches[0].get("group_id")
    write_env(args.github_env, {"EAS_UPDATE_GROUP_ID": str(group_id)})


def parse_update_view(args: argparse.Namespace) -> None:
    data = read_json(args.input)
    matches: list[dict[str, Any]] = []
    for item in _walk(data):
        update_id = item.get("id") or item.get("updateId") or item.get("update_id")
        group_id = item.get("group") or item.get("groupId") or item.get("group_id")
        item_runtime = item.get("runtimeVersion") or item.get("runtime_version")
        platform = item.get("platform")
        if not isinstance(update_id, str) or not update_id:
            continue
        if group_id != args.group_id:
            continue
        if item_runtime != args.runtime_version:
            continue
        if isinstance(platform, str) and platform.lower() != args.platform.lower():
            continue
        if not _message_matches(item.get("message"), args.message):
            continue
        matches.append(item)
    if not matches:
        fail("matching platform-specific EAS update was not found in update group")
    update_id = matches[0].get("id") or matches[0].get("updateId") or matches[0].get("update_id")
    write_env(
        args.github_env,
        {
            "EAS_UPDATE_ID": str(update_id),
            "EAS_UPDATE_GROUP_ID": args.group_id,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--record", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--target-branch", required=True)
    p.add_argument("--wake-title", required=True)
    p.add_argument("--control-sha", required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--github-env", required=True)
    p.set_defaults(func=prepare)

    p = sub.add_parser("check-ota")
    p.add_argument("--repo", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--working-directory", required=True)
    p.set_defaults(func=check_ota)

    p = sub.add_parser("dispatch-payload")
    p.add_argument("--ref", required=True)
    p.add_argument("--inputs-json", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=dispatch_payload)

    p = sub.add_parser("parse-eas-run")
    p.add_argument("--input", required=True)
    p.add_argument("--github-env", required=True)
    p.set_defaults(func=parse_eas_run)

    p = sub.add_parser("extract-provider-run")
    p.add_argument("--input", required=True)
    p.add_argument("--github-env", required=True)
    p.set_defaults(func=extract_provider_run)

    p = sub.add_parser("parse-eas-status")
    p.add_argument("--input", required=True)
    p.add_argument("--github-env", required=True)
    p.set_defaults(func=parse_eas_status)

    p = sub.add_parser("parse-update-group")
    p.add_argument("--input", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--runtime-version", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--github-env", required=True)
    p.set_defaults(func=parse_update_group)

    p = sub.add_parser("parse-update-view")
    p.add_argument("--input", required=True)
    p.add_argument("--group-id", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--runtime-version", required=True)
    p.add_argument("--platform", required=True)
    p.add_argument("--github-env", required=True)
    p.set_defaults(func=parse_update_view)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
