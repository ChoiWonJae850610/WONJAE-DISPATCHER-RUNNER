"""Targeted offline tests of dynamic minimal CI policy and read-only admission."""

import hashlib
import subprocess

import pytest

from wonjae_dispatcher_runner.lean_ci_maintenance import (
    LeanCiMaintenanceError,
    registered_ci,
    review_candidate,
    select_minimal_checks,
)

ORIGINAL = """name: Future Cloud Validation
on:
  push:
    branches: [dev-next]
  pull_request:
    branches: [dev-next]
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Install all
        run: npm ci
      - name: Test all
        run: npm test
"""

PROPOSED = ORIGINAL.replace("      - name: Install all\n        run: npm ci\n", "").replace(
    "run: npm test", "run: node tests/affected.mjs"
)

REGISTRY = """schema_version: 1
projects:
  FIRST:
    status: active
    repository: ExampleTeam/First
    branch: cloud-dev-v1
    ci:
      validation:
        name: First CI
        path: .github/workflows/first.yml
  FUTURE:
    status: active
    repository: ExampleTeam/Future
    branch: dev-next
    ci:
      validation:
        name: Future Cloud Validation
        path: .github/workflows/future.yml
  OLD:
    status: archived
"""


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def checkout(repo, files):
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "baseline")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def workspaces(tmp_path):
    control = tmp_path / "control"
    product = tmp_path / "product"
    c_sha = checkout(control, {"PROJECTS.yaml": REGISTRY})
    p_sha = checkout(product, {".github/workflows/future.yml": ORIGINAL})
    git(control, "remote", "add", "origin", "https://github.com/ChoiWonJae850610/DEV-CONTROL.git")
    git(product, "remote", "add", "origin", "https://github.com/ExampleTeam/Future.git")
    return control, product, c_sha, p_sha


def review(workspaces, content=PROPOSED, **changes):
    control, product, c_sha, p_sha = workspaces
    args = {
        "control_checkout": control,
        "product_checkout": product,
        "project": "FUTURE",
        "expected_control_sha": c_sha,
        "expected_product_sha": p_sha,
        "expected_previous_sha256": hashlib.sha256(ORIGINAL.encode()).hexdigest(),
        "expected_proposed_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "candidate": content,
    }
    args.update(changes)
    return review_candidate(**args)


def test_dynamic_registry_supports_new_active_products(tmp_path):
    path = tmp_path / "PROJECTS.yaml"
    path.write_text(REGISTRY)
    projects = registered_ci(path)
    assert list(projects) == ["FIRST", "FUTURE"]
    assert projects["FUTURE"].repository == "ExampleTeam/Future"
    assert projects["FUTURE"].validation_path == ".github/workflows/future.yml"


@pytest.mark.parametrize(("paths", "category"), [
    (("docs/NEXT_WORK.md", ".wonjae/execution-state.yaml"), "docs"),
    (("supabase/migrations/012.sql",), "sensitive_source"),
    (("src/auth/check.ts",), "sensitive_source"),
    (("src/feature.cs",), "affected_source"),
    (("game/world.tscn",), "affected_source"),
    ((".github/workflows/future.yml",), "workflow_maintenance"),
])
def test_change_focused_profiles(paths, category):
    plan = select_minimal_checks(paths)
    assert plan.category == category
    assert plan.required
    assert "full_suite" not in plan.required


@pytest.mark.parametrize("paths", [
    (), ("docs/a.md", "docs/a.md"), ("../escape",),
    ("/absolute",), ("bad\\path",), ("bad//path",),
])
def test_unsafe_changed_paths_rejected(paths):
    with pytest.raises(LeanCiMaintenanceError):
        select_minimal_checks(paths)


def test_review_only_does_not_write_product(workspaces):
    result = review(workspaces)
    assert result.project == "FUTURE"
    assert result.workflow_path == ".github/workflows/future.yml"
    assert result.mode == "REVIEW_ONLY_NOT_EXECUTABLE"
    _, product, _, _ = workspaces
    assert (product / result.workflow_path).read_text() == ORIGINAL
    assert git(product, "status", "--porcelain") == ""


@pytest.mark.parametrize("candidate", [
    ORIGINAL.replace("contents: read", "contents: write"),
    ORIGINAL.replace("  pull_request:", "  pull_request_target:"),
    ORIGINAL.replace("runs-on: ubuntu-latest", "runs-on: self-hosted"),
    ORIGINAL.replace("name: Future Cloud Validation", "name: Renamed"),
    ORIGINAL.replace("uses: actions/checkout@v4", "uses: another/action@v1"),
    ORIGINAL.replace("  contents: read", "  contents: read\n  contents: write"),
    ORIGINAL,
])
def test_review_fails_closed_on_authority_changes(workspaces, candidate):
    with pytest.raises(LeanCiMaintenanceError):
        review(workspaces, content=candidate)


def test_review_requires_exact_git_heads_and_hashes(workspaces):
    with pytest.raises(LeanCiMaintenanceError, match="checkout moved"):
        review(workspaces, expected_product_sha="a" * 40)
    with pytest.raises(LeanCiMaintenanceError, match="digest"):
        review(workspaces, expected_proposed_sha256="0" * 64)


def test_review_rejects_unknown_target_and_dirty_checkout(workspaces):
    with pytest.raises(LeanCiMaintenanceError, match="not active"):
        review(workspaces, project="UNKNOWN")
    _, product, _, _ = workspaces
    (product / "README.md").write_text("dirty")
    with pytest.raises(LeanCiMaintenanceError, match="dirty"):
        review(workspaces)


def test_registry_rejects_escape_from_workflow_directory(tmp_path):
    path = tmp_path / "PROJECTS.yaml"
    path.write_text(REGISTRY.replace("future.yml", "../other.yml"))
    with pytest.raises(LeanCiMaintenanceError, match="validation path"):
        registered_ci(path)


def test_wrong_repository_origin_is_rejected(workspaces):
    _, product, _, _ = workspaces
    git(product, "remote", "set-url", "origin", "https://github.com/Elsewhere/Unrelated.git")
    with pytest.raises(LeanCiMaintenanceError, match="origin"):
        review(workspaces)
