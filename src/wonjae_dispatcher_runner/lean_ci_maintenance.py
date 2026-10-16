"""Read-only, registry-scoped admission for future Owner-run CI maintenance.

This module NEVER writes product source, dispatches Actions, merges a PR, or
changes the Direct Worker .github protection. The trusted, authenticated Owner
route and a deterministic source-writing executor are separate later stages.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml


class LeanCiMaintenanceError(ValueError):
    """Invalid scope, exact source, or proposed CI workflow."""


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_VALIDATION_PATH = re.compile(r"\.github/workflows/[a-zA-Z0-9_.-]+\.ya?ml\Z")
_FORBIDDEN_SECRET = re.compile(r"\$\{\{\s*secrets\.", re.IGNORECASE)
_MAX_CANDIDATE_BYTES = 96_000


@dataclass(frozen=True)
class RegisteredCi:
    project: str
    repository: str
    branch: str
    validation_name: str
    validation_path: str


@dataclass(frozen=True)
class LeanSelection:
    category: str
    required: tuple[str, ...]
    normally_skip: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowPreview:
    project: str
    repository: str
    branch: str
    source_sha: str
    control_sha: str
    validation_name: str
    workflow_path: str
    previous_sha256: str
    proposed_sha256: str
    mode: str = "REVIEW_ONLY_NOT_EXECUTABLE"


class _UniqueYamlLoader(yaml.BaseLoader):
    """Parse GitHub workflow 'on' literally and reject duplicate YAML keys."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        if not isinstance(node, yaml.MappingNode):
            raise LeanCiMaintenanceError("workflow requires YAML mappings")
        result: dict[str, object] = {}
        for raw_key, raw_value in node.value:
            key = self.construct_object(raw_key, deep=deep)
            if not isinstance(key, str) or key in result:
                raise LeanCiMaintenanceError("workflow has duplicate or non-string YAML key")
            result[key] = self.construct_object(raw_value, deep=deep)
        return result


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise LeanCiMaintenanceError(f"{label} must be a lowercase 40-character Git SHA")
    return value


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise LeanCiMaintenanceError("invalid Git repository path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
        part in {"..", "."} for part in value.split("/")
    ):
        raise LeanCiMaintenanceError("unsafe Git repository path")
    return value


def registered_ci(registry_file: Path) -> dict[str, RegisteredCi]:
    """Read ALL active projects from current PROJECTS.yaml; never hardcode names."""
    try:
        data = yaml.safe_load(registry_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LeanCiMaintenanceError("control registry cannot be read") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise LeanCiMaintenanceError("unsupported control registry")
    entries = data.get("projects")
    if not isinstance(entries, dict) or not entries:
        raise LeanCiMaintenanceError("control registry has no products")

    found: dict[str, RegisteredCi] = {}
    for project, info in entries.items():
        if not isinstance(info, dict) or info.get("status") != "active":
            continue
        if not isinstance(project, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_-]{0,63}", project
        ):
            raise LeanCiMaintenanceError("active project key is invalid")
        repository = info.get("repository")
        branch = info.get("branch")
        validation = (info.get("ci") or {}).get("validation")
        if not isinstance(repository, str) or not _REPO.fullmatch(repository):
            raise LeanCiMaintenanceError(f"{project}: invalid registered repository")
        if not isinstance(branch, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch
        ) or ".." in branch or branch.endswith("/"):
            raise LeanCiMaintenanceError(f"{project}: invalid registered branch")
        if not isinstance(validation, dict):
            raise LeanCiMaintenanceError(f"{project}: no canonical validation")
        workflow = validation.get("path")
        name = validation.get("name")
        if not isinstance(workflow, str) or not _VALIDATION_PATH.fullmatch(workflow):
            raise LeanCiMaintenanceError(f"{project}: invalid validation path")
        if not isinstance(name, str) or not name.strip():
            raise LeanCiMaintenanceError(f"{project}: no canonical workflow name")
        found[project] = RegisteredCi(
            project=project,
            repository=repository,
            branch=branch,
            validation_name=name,
            validation_path=workflow,
        )
    if not found:
        raise LeanCiMaintenanceError("no active products")
    return found


def select_minimal_checks(paths: tuple[str, ...]) -> LeanSelection:
    """Propose small checks, never a CI pass or automatic full-suite escalation."""
    if not paths or len(paths) > 100 or len(set(paths)) != len(paths):
        raise LeanCiMaintenanceError("changed path set is empty, duplicated or too large")
    checked = tuple(_relative(path) for path in paths)
    if any(p.startswith(".github/") for p in checked):
        return LeanSelection(
            "workflow_maintenance",
            ("registered_workflow_scope", "workflow_security_diff", "exact_head_ci"),
            ("automatic_full_historical_suite", "provider_dispatch"),
        )

    def is_document(path: str) -> bool:
        return path.startswith("docs/") or path.endswith(".md") or (
            path == ".wonjae/execution-state.yaml"
        )

    if all(is_document(p) for p in checked):
        return LeanSelection(
            "docs",
            ("changed_document_syntax", "registered_state_shape", "git_identity"),
            ("npm_install", "chromium", "expo_export", "godot_export", "full_suite"),
        )

    risky = (
        "migration", "supabase/", "auth", "rls", "permission", "provider",
        "secret", "credential", "deploy", "release", "storage", "policy",
        "signing", "entitlement",
    )
    if any(p.endswith(".sql") or any(word in p.lower() for word in risky)
           for p in checked):
        return LeanSelection(
            "sensitive_source",
            ("changed_contract", "relevant_auth_data_security_guards", "git_identity"),
            ("unrelated_ui_tests", "automatic_all_history", "unrequested_live_provider"),
        )

    return LeanSelection(
        "affected_source",
        ("affected_feature_tests", "relevant_type_or_build_smoke", "git_identity"),
        ("unrelated_feature_tests", "chromium_unless_affected",
         "expo_or_godot_export_unless_affected", "automatic_full_suite"),
    )


def _git_head(repo: Path) -> str:
    if (repo.is_symlink() or not repo.is_dir() or
            not (repo / ".git").is_dir() or (repo / ".git").is_symlink()):
        raise LeanCiMaintenanceError("must use a normal exact Git checkout")
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise LeanCiMaintenanceError("cannot read checkout HEAD") from exc
    return _sha(p.stdout.strip(), "Git checkout HEAD")


def _verify_origin(repo: Path, expected_repository: str) -> None:
    """Bind local checkout to the registered GitHub repository, without network."""
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            check=True, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise LeanCiMaintenanceError("checkout has no verifiable origin") from exc
    value = p.stdout.strip()
    prefixes = ("https://github.com/", "git@github.com:")
    for prefix in prefixes:
        if value.startswith(prefix):
            actual = value.removeprefix(prefix).removesuffix(".git")
            if actual == expected_repository:
                return
    raise LeanCiMaintenanceError("checkout origin is not the registered repository")


def _require_clean(repo: Path) -> None:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise LeanCiMaintenanceError("cannot inspect product Git worktree") from exc
    if p.stdout:
        raise LeanCiMaintenanceError("product checkout is dirty")


def _workflow(content: str) -> dict:
    if not content or len(content.encode("utf-8")) > _MAX_CANDIDATE_BYTES:
        raise LeanCiMaintenanceError("workflow is missing or too large")
    if _FORBIDDEN_SECRET.search(content):
        raise LeanCiMaintenanceError("workflow may not add GitHub secret expressions")
    try:
        data = yaml.load(content, Loader=_UniqueYamlLoader)
    except (yaml.YAMLError, LeanCiMaintenanceError) as exc:
        raise LeanCiMaintenanceError("workflow YAML is invalid or ambiguous") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise LeanCiMaintenanceError("workflow jobs are missing")
    return data


def _check_workflow_change(old: str, candidate: str, expected_name: str) -> None:
    before, after = _workflow(old), _workflow(candidate)
    if old == candidate:
        raise LeanCiMaintenanceError("candidate workflow has no changes")
    if before.get("name") != expected_name or after.get("name") != expected_name:
        raise LeanCiMaintenanceError("canonical workflow identity changed")

    # No new trigger, permissions, environment, reusable job, or runner class.
    if {k: v for k, v in before.items() if k != "jobs"} != {
        k: v for k, v in after.items() if k != "jobs"
    }:
        raise LeanCiMaintenanceError("workflow top-level authority/trigger changed")
    if set(before["jobs"]) != set(after["jobs"]):
        raise LeanCiMaintenanceError("job identities changed")
    for job_name, previous in before["jobs"].items():
        proposed = after["jobs"][job_name]
        if not isinstance(previous, dict) or not isinstance(proposed, dict):
            raise LeanCiMaintenanceError("workflow job must remain a mapping")
        if {k: v for k, v in previous.items() if k != "steps"} != {
            k: v for k, v in proposed.items() if k != "steps"
        }:
            raise LeanCiMaintenanceError("job-level authority/runtime changed")
        old_steps, new_steps = previous.get("steps"), proposed.get("steps")
        if not isinstance(old_steps, list) or not isinstance(new_steps, list):
            raise LeanCiMaintenanceError("workflow steps are missing")
        if not new_steps or len(new_steps) > len(old_steps) + 5:
            raise LeanCiMaintenanceError("unexpected CI step expansion")
        if any(not isinstance(step, dict) for step in (*old_steps, *new_steps)):
            raise LeanCiMaintenanceError("workflow steps must be mappings")
        allowed_actions = {step["uses"] for step in old_steps if "uses" in step}
        if any(step.get("uses") not in allowed_actions for step in new_steps
               if "uses" in step):
            raise LeanCiMaintenanceError("new external Actions dependencies are forbidden")


def review_candidate(
    *,
    control_checkout: Path,
    product_checkout: Path,
    project: str,
    expected_control_sha: str,
    expected_product_sha: str,
    expected_previous_sha256: str,
    expected_proposed_sha256: str,
    candidate: str,
) -> WorkflowPreview:
    """Review only. Future Owner-authorized writing/PR/CI integration is separate."""
    control_sha = _sha(expected_control_sha, "control SHA")
    source_sha = _sha(expected_product_sha, "product SHA")
    if _git_head(control_checkout) != control_sha or _git_head(product_checkout) != source_sha:
        raise LeanCiMaintenanceError("checkout moved since exact SHA admission")
    registered = registered_ci(control_checkout / "PROJECTS.yaml")
    if project not in registered:
        raise LeanCiMaintenanceError("product is not active in the current registry")
    target = registered[project]
    _verify_origin(control_checkout, "ChoiWonJae850610/DEV-CONTROL")
    _verify_origin(product_checkout, target.repository)
    _require_clean(product_checkout)
    relative = _relative(target.validation_path)
    original_path = product_checkout / relative
    if original_path.is_symlink() or not original_path.is_file():
        raise LeanCiMaintenanceError("registered workflow must be an existing regular file")
    root = product_checkout.resolve()
    if original_path.resolve() != root / relative:
        raise LeanCiMaintenanceError("registered workflow is aliased or escapes checkout")
    try:
        original = original_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LeanCiMaintenanceError("registered workflow is unreadable") from exc
    before_digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    after_digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    if before_digest != expected_previous_sha256 or after_digest != expected_proposed_sha256:
        raise LeanCiMaintenanceError("workflow content digest does not match reviewed inputs")
    _check_workflow_change(original, candidate, target.validation_name)
    return WorkflowPreview(
        project=target.project, repository=target.repository, branch=target.branch,
        source_sha=source_sha, control_sha=control_sha,
        validation_name=target.validation_name, workflow_path=target.validation_path,
        previous_sha256=before_digest, proposed_sha256=after_digest,
    )
