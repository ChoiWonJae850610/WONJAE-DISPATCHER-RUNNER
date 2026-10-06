from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml
from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

from .repair_timeout import RepairPlanTimeout, repair_plan_deadline

RUNNER_REPOSITORY = "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER"
RUNNER_WORKFLOW = ".github/workflows/direct-worker.yml"
ALLOWED_COMMANDS = {"next", "retry", "resume"}
HANDOFF_ACTION_TYPES = {"SOURCE_READY", "MANUAL_QA", "PROVIDER_GATE", "DECISION_REQUIRED", "NONE"}
PROTECTED_SOURCE_PATHS = {"AGENTS.md", "PROJECT_RULES.md", ".gitmodules"}
PROTECTED_SOURCE_PREFIXES = (".github/",)



class DirectWorkerError(RuntimeError):
    """Raised when a Direct Worker request violates the registered contract."""


@dataclass(frozen=True)
class DirectWorkerHandoff:
    project: str
    updated_at: str
    repository: str
    branch: str
    current_head: str
    source_validation_result: str
    source_validation_run_id: int
    next_action_type: str
    next_action_title: str
    source_task_id: str | None
    owner_action: str | None
    source_scope: tuple[str, ...]


@dataclass(frozen=True)
class DirectWorkerRoute:
    project: str
    repository: str
    branch: str
    startup_entry: str
    project_rules: str
    canonical_docs: tuple[str, ...]
    validation_workflow_path: str
    validation_workflow_name: str
    runner_repository: str
    runner_workflow: str
    handoff_path: str | None
    handoff: DirectWorkerHandoff | None


@dataclass(frozen=True)
class DirectWorkerResult:
    status: str
    summary: str
    manual_action: str
    changed_paths: tuple[str, ...]


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    return result.stdout


def git_head(repo_path: Path) -> str:
    value = _git(repo_path, "rev-parse", "HEAD").strip()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise DirectWorkerError("product checkout HEAD is not a commit SHA")
    return value


def _safe_relative(path: str, label: str) -> None:
    value = Path(path)
    if value.is_absolute() or ".." in value.parts or not path.strip():
        raise DirectWorkerError(f"{label} contains an unsafe repository path")


def changed_paths(repo_path: Path) -> tuple[str, ...]:
    tracked = _git(repo_path, "diff", "--name-only", "-z", "--")
    staged = _git(repo_path, "diff", "--cached", "--name-only", "-z", "--")
    untracked = _git(repo_path, "ls-files", "--others", "--exclude-standard", "-z")
    paths = tuple(
        dict.fromkeys(
            filter(
                None,
                (
                    *tracked.split("\x00"),
                    *staged.split("\x00"),
                    *untracked.split("\x00"),
                ),
            )
        )
    )
    root = repo_path.resolve()
    for relative in paths:
        _safe_relative(relative, "worktree")
        target = repo_path / relative
        probe = target if target.exists() or target.is_symlink() else target.parent
        resolved = probe.resolve()
        if not resolved.is_relative_to(root):
            raise DirectWorkerError("worktree path escapes the product repository")
        if target.is_symlink():
            raise DirectWorkerError("Direct Worker may not mutate a symlink path")
    protected = [
        path
        for path in paths
        if path in PROTECTED_SOURCE_PATHS
        or any(path.startswith(prefix) for prefix in PROTECTED_SOURCE_PREFIXES)
    ]
    if protected:
        raise DirectWorkerError("Direct Worker attempted to change a protected source path")
    return paths


def git_metadata_snapshot(repo_path: Path) -> str:
    git_dir = repo_path / ".git"
    if not git_dir.is_dir():
        raise DirectWorkerError("Direct Worker requires a normal Git checkout")

    watched = [git_dir / "config", git_dir / "HEAD"]
    head_text = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if head_text.startswith("ref: "):
        relative_ref = head_text.removeprefix("ref: ").strip()
        _safe_relative(relative_ref, "git ref")
        watched.append(git_dir / relative_ref)

    hooks_dir = git_dir / "hooks"
    if hooks_dir.is_dir():
        watched.extend(sorted(path for path in hooks_dir.rglob("*") if path.is_file()))

    digest = hashlib.sha256()
    for path in watched:
        relative = path.relative_to(git_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def _load_execution_handoff(
    registry_path: Path,
    handoff_path: str,
    project: str,
    repository: str,
    branch: str,
) -> DirectWorkerHandoff:
    _safe_relative(handoff_path, "handoff")
    path = registry_path.parent / handoff_path
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DirectWorkerError("registered execution handoff is missing") from exc
    except yaml.YAMLError as exc:
        raise DirectWorkerError("execution handoff YAML is invalid") from exc
    if not isinstance(payload, dict):
        raise DirectWorkerError("execution handoff must be a mapping")
    if payload.get("schema_version") != 1:
        raise DirectWorkerError("execution handoff schema_version must be 1")
    if payload.get("project") != project:
        raise DirectWorkerError("execution handoff project mismatch")
    if payload.get("repository") != repository:
        raise DirectWorkerError("execution handoff repository mismatch")
    if payload.get("current_branch") != branch:
        raise DirectWorkerError("execution handoff branch mismatch")

    current_head = payload.get("current_head")
    if not isinstance(current_head, str) or len(current_head) != 40 or any(
        ch not in "0123456789abcdef" for ch in current_head
    ):
        raise DirectWorkerError("execution handoff current_head is invalid")
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, str) or "T" not in updated_at:
        raise DirectWorkerError("execution handoff updated_at is invalid")

    source_validation = payload.get("source_validation")
    if not isinstance(source_validation, dict):
        raise DirectWorkerError("execution handoff source_validation is missing")
    validation_result = source_validation.get("result")
    validation_run_id = source_validation.get("run_id")
    if validation_result != "PASS":
        raise DirectWorkerError("execution handoff requires PASS source validation")
    if not isinstance(validation_run_id, int) or validation_run_id < 1:
        raise DirectWorkerError("execution handoff validation run_id is invalid")

    next_action = payload.get("next_action")
    if not isinstance(next_action, dict):
        raise DirectWorkerError("execution handoff next_action is missing")
    action_type = next_action.get("type")
    action_title = next_action.get("title")
    if action_type not in HANDOFF_ACTION_TYPES:
        raise DirectWorkerError("execution handoff next_action type is invalid")
    if not isinstance(action_title, str) or not action_title.strip():
        raise DirectWorkerError("execution handoff next_action title is invalid")

    source_task_id = next_action.get("source_task_id")
    if source_task_id is not None and (
        not isinstance(source_task_id, str) or not source_task_id.strip()
    ):
        raise DirectWorkerError("execution handoff source_task_id is invalid")
    owner_action = next_action.get("owner_action")
    if owner_action is not None and (
        not isinstance(owner_action, str) or not owner_action.strip()
    ):
        raise DirectWorkerError("execution handoff owner_action is invalid")
    source_scope = next_action.get("source_scope", [])
    if not isinstance(source_scope, list) or not all(
        isinstance(item, str) and item.strip() for item in source_scope
    ):
        raise DirectWorkerError("execution handoff source_scope is invalid")
    if action_type == "SOURCE_READY" and not source_scope:
        raise DirectWorkerError("SOURCE_READY handoff requires source_scope")

    return DirectWorkerHandoff(
        project=project,
        updated_at=updated_at,
        repository=repository,
        branch=branch,
        current_head=current_head,
        source_validation_result=validation_result,
        source_validation_run_id=validation_run_id,
        next_action_type=action_type,
        next_action_title=action_title.strip(),
        source_task_id=source_task_id.strip() if isinstance(source_task_id, str) else None,
        owner_action=owner_action.strip() if isinstance(owner_action, str) else None,
        source_scope=tuple(item.strip() for item in source_scope),
    )


def require_next_source_ready(route: DirectWorkerRoute, starting_sha: str) -> None:
    handoff = route.handoff
    if handoff is None:
        return
    if handoff.current_head != starting_sha:
        raise DirectWorkerError(
            "execution handoff is stale for the current product HEAD; reconcile it first"
        )
    if handoff.next_action_type != "SOURCE_READY":
        raise DirectWorkerError(
            f"next source work is blocked by execution handoff: {handoff.next_action_type}"
        )


def load_direct_worker_route(registry_path: Path, project: str) -> DirectWorkerRoute:
    if project == "KDN":
        raise DirectWorkerError("KDN is excluded from Direct Worker")
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    projects = payload.get("projects") if isinstance(payload, dict) else None
    value = projects.get(project) if isinstance(projects, dict) else None
    if not isinstance(value, dict) or value.get("status") != "active":
        raise DirectWorkerError("project is not an active registry entry")

    execution = value.get("execution")
    if not isinstance(execution, dict) or execution.get("mode") != "direct_worker":
        raise DirectWorkerError("project is not registered for Direct Worker")
    if execution.get("source_writer_concurrency") != 1:
        raise DirectWorkerError("Direct Worker requires one source writer per project")
    if execution.get("cross_project_parallel") is not True:
        raise DirectWorkerError("Direct Worker registry must allow cross-project parallelism")
    if execution.get("runner_repository") != RUNNER_REPOSITORY:
        raise DirectWorkerError("Direct Worker runner repository mismatch")
    if execution.get("runner_workflow") != RUNNER_WORKFLOW:
        raise DirectWorkerError("Direct Worker runner workflow mismatch")

    repository = value.get("repository")
    branch = value.get("branch")
    startup_entry = value.get("startup_entry")
    project_rules = value.get("project_rules")
    canonical_docs = value.get("canonical_docs")
    validation = (
        value.get("ci", {}).get("validation")
        if isinstance(value.get("ci"), dict)
        else None
    )
    if not isinstance(repository, str) or "/" not in repository:
        raise DirectWorkerError("registry repository is invalid")
    if not isinstance(branch, str) or not branch.strip():
        raise DirectWorkerError("registry branch is invalid")
    if not isinstance(startup_entry, str) or not isinstance(project_rules, str):
        raise DirectWorkerError("registry startup files are invalid")
    if not isinstance(canonical_docs, list) or not canonical_docs or not all(
        isinstance(item, str) and item.strip() for item in canonical_docs
    ):
        raise DirectWorkerError("registry canonical_docs are invalid")
    if not isinstance(validation, dict):
        raise DirectWorkerError("registry validation route is missing")
    workflow_path = validation.get("path")
    workflow_name = validation.get("name")
    if not isinstance(workflow_path, str) or not workflow_path.startswith(".github/workflows/"):
        raise DirectWorkerError("registry validation workflow path is invalid")
    if not isinstance(workflow_name, str) or not workflow_name.strip():
        raise DirectWorkerError("registry validation workflow name is invalid")

    for relative in (startup_entry, project_rules, *canonical_docs):
        _safe_relative(relative, "registry")

    handoff_path = execution.get("handoff_path")
    handoff = None
    if handoff_path is not None:
        if not isinstance(handoff_path, str) or not handoff_path.strip():
            raise DirectWorkerError("registry handoff_path is invalid")
        handoff = _load_execution_handoff(
            registry_path,
            handoff_path,
            project,
            repository,
            branch,
        )

    return DirectWorkerRoute(
        project=project,
        repository=repository,
        branch=branch,
        startup_entry=startup_entry,
        project_rules=project_rules,
        canonical_docs=tuple(canonical_docs),
        validation_workflow_path=workflow_path,
        validation_workflow_name=workflow_name,
        runner_repository=execution["runner_repository"],
        runner_workflow=execution["runner_workflow"],
        handoff_path=handoff_path,
        handoff=handoff,
    )


def _prompt(
    route: DirectWorkerRoute,
    command: str,
    starting_sha: str,
    validation_failure: str,
) -> str:
    docs = "\n".join(f"- {path}" for path in route.canonical_docs)
    failure = validation_failure[-80_000:] if validation_failure else "(none)"
    handoff = route.handoff
    if command == "next" and handoff is not None:
        scope = "\n".join(f"- {item}" for item in handoff.source_scope)
        retry_note = (
            f"Execute exactly the SOURCE_READY handoff task: {handoff.next_action_title}\n"
            f"Source task ID: {handoff.source_task_id or '(none)'}\n"
            f"Authorized source scope:\n{scope}"
        )
    else:
        retry_note = (
            "This is a retry/resume of the one existing Direct Worker PR. "
            "Preserve correct prior work and fix the current task in place."
            if command in {"retry", "resume"}
            else
            "Select the smallest complete already-decided next SOURCE task from the "
            "current canonical repository documents."
        )
    return f"""You are the single source-writing Direct Worker for {route.project}.

The Owner has authorized command: {command}.
Repository: {route.repository}
Active development branch: {route.branch}
Exact starting checkout SHA: {starting_sha}

Before editing, read these files from the checkout:
- {route.startup_entry}
- {route.project_rules}
{docs}

Then inspect task-relevant source and repository-owned current/next-work documentation.
GitHub checkout state and repository safety/approval rules are authoritative; chat history is not.

For command next, the registered DEV-CONTROL execution handoff is the current
execution-routing authority. Its exact HEAD, validation evidence, action type, task title
and source scope supersede older task-state, "current task", "next task", attempt or
sequencing prose retained in product documents or project-rules history. Historical task
sections remain evidence only. Product rules still govern durable safety, provider,
Production, credential, destructive-operation and physical/device approval boundaries.
A SOURCE_READY handoff explicitly authorizes source-only schema/migration FILE preparation
inside its source scope; it never authorizes applying that migration to a live provider.

{retry_note}

Authority and hard boundaries:
- Work only inside this repository checkout.
- Make source/test/document changes needed for that one already-decided task.
- Do not invent a new feature, policy, architecture direction, or product scope.
- Do not commit, push, merge, create releases/tags, or modify Git history.
- Do not perform provider, deployment, Production, credential, payment, signing, device,
  physical-QA, destructive database, or destructive Git actions.
- Do not use network access or try to bypass the sandbox.
- A source migration FILE may be edited only when already authorized by the product rules;
  never apply it to a live database from this worker.
- If the next step requires Owner/provider/Production/paid/credential/device/physical authority,
  or the canonical state is ambiguous, do not fabricate progress. Return MANUAL_REQUIRED with
  the single smallest Owner action. Source changes already safely completed before discovering
  that gate may remain in the worktree.
- Local tests may be run when dependencies are already present. Do not install dependencies
  from the network. GitHub Actions remains the exact-SHA validation authority.

Latest exact validation failure supplied for retry, if any:
{failure}

Finish with the structured result requested by the caller.
"""


def _parse_result(response: str) -> tuple[str, str, str]:
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as exc:
        raise DirectWorkerError("Codex Direct Worker result was not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"status", "summary", "manual_action"}:
        raise DirectWorkerError("Codex Direct Worker result shape was invalid")
    status = payload.get("status")
    summary = payload.get("summary")
    manual_action = payload.get("manual_action")
    if status not in {"CHANGED", "MANUAL_REQUIRED", "NO_WORK"}:
        raise DirectWorkerError("Codex Direct Worker status was invalid")
    if not isinstance(summary, str) or not summary.strip():
        raise DirectWorkerError("Codex Direct Worker summary was missing")
    if not isinstance(manual_action, str):
        raise DirectWorkerError("Codex Direct Worker manual_action was invalid")
    if status == "MANUAL_REQUIRED" and not manual_action.strip():
        raise DirectWorkerError("MANUAL_REQUIRED requires one smallest Owner action")
    if status != "MANUAL_REQUIRED" and manual_action.strip():
        raise DirectWorkerError("manual_action is allowed only for MANUAL_REQUIRED")
    return status, summary.strip(), manual_action.strip()


def run_direct_worker(
    repo_path: Path,
    route: DirectWorkerRoute,
    command: str,
    codex_home: Path,
    validation_failure: str = "",
) -> DirectWorkerResult:
    if command not in ALLOWED_COMMANDS:
        raise DirectWorkerError("unsupported Direct Worker command")
    starting_sha = git_head(repo_path)
    if command == "next":
        require_next_source_ready(route, starting_sha)
    if changed_paths(repo_path):
        raise DirectWorkerError("product checkout must be clean before Direct Worker")
    git_metadata = git_metadata_snapshot(repo_path)

    safe_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    safe_home = os.environ.get("HOME", str(Path.home()))
    safe_lang = os.environ.get("LANG", "C.UTF-8")
    safe_ssl_file = os.environ.get("SSL_CERT_FILE")
    safe_ssl_dir = os.environ.get("SSL_CERT_DIR")

    # The Codex app-server must not inherit GitHub/provider tokens or the serialized
    # ChatGPT credential. Authentication has already been restored into CODEX_HOME.
    os.environ.clear()
    os.environ.update(
        {
            "PATH": safe_path,
            "HOME": safe_home,
            "LANG": safe_lang,
            "TZ": "UTC",
            "CODEX_HOME": str(codex_home),
            "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
        }
    )
    if safe_ssl_file:
        os.environ["SSL_CERT_FILE"] = safe_ssl_file
    if safe_ssl_dir:
        os.environ["SSL_CERT_DIR"] = safe_ssl_dir

    config = CodexConfig(
        cwd=str(repo_path),
        env={
            "PATH": safe_path,
            "HOME": safe_home,
            "LANG": safe_lang,
            "TZ": "UTC",
            "CODEX_HOME": str(codex_home),
            "CODEX_APP_SERVER_DISABLE_MANAGED_CONFIG": "1",
        },
    )
    output_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["CHANGED", "MANUAL_REQUIRED", "NO_WORK"],
            },
            "summary": {"type": "string", "minLength": 1},
            "manual_action": {"type": "string"},
        },
        "required": ["status", "summary", "manual_action"],
        "additionalProperties": False,
    }
    thread_config = {
        "history": {"persistence": "none"},
        "allow_login_shell": False,
        "sandbox_workspace_write": {"network_access": False},
        "shell_environment_policy": {
            "inherit": "none",
            "set": {
                "PATH": safe_path,
                "HOME": safe_home,
                "LANG": safe_lang,
                "TZ": "UTC",
            },
        },
    }

    try:
        with repair_plan_deadline(), Codex(config=config) as codex:
            account = codex.account(refresh_token=False)
            if account.account is None:
                raise DirectWorkerError("Codex account session is missing")
            thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=str(repo_path),
                ephemeral=True,
                sandbox=Sandbox.workspace_write,
                config=thread_config,
            )
            result = thread.run(
                _prompt(route, command, starting_sha, validation_failure),
                approval_mode=ApprovalMode.deny_all,
                output_schema=output_schema,
                sandbox=Sandbox.workspace_write,
            )
    except RepairPlanTimeout as exc:
        raise DirectWorkerError("Direct Worker Codex turn timed out") from exc

    status_value = str(getattr(result.status, "value", result.status)).lower()
    if status_value != "completed" or result.error is not None:
        raise DirectWorkerError("Direct Worker Codex turn did not complete")
    if git_metadata_snapshot(repo_path) != git_metadata:
        raise DirectWorkerError("Codex modified protected Git metadata")
    if git_head(repo_path) != starting_sha:
        raise DirectWorkerError(
            "Codex changed Git history; Direct Worker requires trusted Git writes"
        )

    paths = changed_paths(repo_path)
    status, summary, manual_action = _parse_result(result.final_response or "")
    if status == "CHANGED" and not paths:
        raise DirectWorkerError("Codex reported CHANGED but produced no worktree changes")
    if status == "NO_WORK" and paths:
        raise DirectWorkerError("Codex reported NO_WORK but changed the worktree")

    return DirectWorkerResult(
        status=status,
        summary=summary,
        manual_action=manual_action,
        changed_paths=paths,
    )
