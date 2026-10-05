import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from wonjae_dispatcher_runner import product_patch as patch
from wonjae_dispatcher_runner.repair_completeness import (
    RepairCompletenessError,
    check_repair_completeness,
    collect_repair_evidence,
)

MIGRATION = "db/migrations/002_feature.sql"
MANIFEST = "db/migrations/SHA256SUMS"
VALIDATOR = "scripts/validate-repository.py"
SQL = "SELECT 2;\n"
CHECKSUM = hashlib.sha256(SQL.encode()).hexdigest() + "  002_feature.sql\n"
CHECKSUM_FAILURE = "Migration 002_feature.sql checksum missing from manifest"


def put(repo, path, content):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def guard(repo, failure, allowed, proposed):
    check_repair_completeness(
        repo, allowed, collect_repair_evidence(failure, allowed), proposed, tuple(proposed)
    )


def test_a_partial_aggregate_rejected_before_write(tmp_path):
    allowed = ("docs/A.md", "src/B.py", "tests/C.py")
    failure = "Error: Feature missing mandatory source artifacts:\n" + "\n".join(allowed)
    with pytest.raises(RepairCompletenessError, match="src/B.py"):
        guard(tmp_path, failure, allowed, {allowed[0]: "complete A\n"})
    assert list(tmp_path.iterdir()) == []
    guard(tmp_path, failure, allowed, dict.fromkeys(allowed, "complete\n"))


def test_b_diagnostic_only_does_not_fix_missing_checksum(tmp_path):
    put(tmp_path, MIGRATION, SQL)
    put(tmp_path, MANIFEST, "")
    with pytest.raises(RepairCompletenessError, match="SHA256SUMS"):
        guard(tmp_path, CHECKSUM_FAILURE, (MIGRATION, MANIFEST, VALIDATOR),
              {VALIDATOR: 'raise ValueError("more specific diagnostic")\n'})


def test_c_exact_manifest_repair_allowed(tmp_path):
    put(tmp_path, MIGRATION, SQL)
    put(tmp_path, MANIFEST, "")
    guard(tmp_path, CHECKSUM_FAILURE, (MIGRATION, MANIFEST), {MANIFEST: CHECKSUM})


@pytest.mark.parametrize("incorrect", ["0" * 64 + "  002_feature.sql\n", CHECKSUM * 2])
def test_stale_or_duplicate_checksum_rejected(tmp_path, incorrect):
    put(tmp_path, MIGRATION, SQL)
    with pytest.raises(RepairCompletenessError, match="exact bytes"):
        guard(tmp_path, CHECKSUM_FAILURE, (MIGRATION, MANIFEST), {MANIFEST: incorrect})


def test_d_validator_bug_evidenced_by_existing_artifact(tmp_path):
    # Exact-head diagnostic claims a required file is absent, but it exists and is
    # nonempty in that very checkout. No source obligation is unresolved.
    artifact = "docs/A.md"
    put(tmp_path, artifact, "canonical artifact\n")
    put(tmp_path, VALIDATOR, 'assert not Path("docs/A.md").is_file()\n')
    guard(tmp_path, f"Required source file is missing: {artifact}", (artifact, VALIDATOR),
          {VALIDATOR: 'assert Path("docs/A.md").is_file()\n'})


def test_validator_bug_evidenced_by_already_valid_checksums(tmp_path):
    put(tmp_path, MIGRATION, SQL)
    put(tmp_path, MANIFEST, CHECKSUM)
    guard(tmp_path, CHECKSUM_FAILURE, (MIGRATION, MANIFEST, VALIDATOR),
          {VALIDATOR: "# correct comparison against canonical source\n"})


def test_validator_edit_without_verifiable_defect_evidence_rejected(tmp_path):
    with pytest.raises(RepairCompletenessError, match="defect evidence"):
        guard(tmp_path, "opaque validation failure", (VALIDATOR,),
              {VALIDATOR: "# diagnostics only\n"})


def test_e_already_correct_path_requires_no_touch(tmp_path):
    allowed = ("docs/A.md", "src/B.py", "tests/C.py")
    put(tmp_path, allowed[0], "correct\n")
    failure = "Missing paths: " + ", ".join(allowed)
    guard(tmp_path, failure, allowed, dict.fromkeys(allowed[1:], "complete\n"))
    assert (tmp_path / allowed[0]).read_text() == "correct\n"


def test_new_migration_requires_dependency_closed_manifest_even_before_checksum_failure(tmp_path):
    put(tmp_path, MANIFEST, "")
    failure = f"Missing file: {MIGRATION}"
    with pytest.raises(RepairCompletenessError, match=hashlib.sha256(SQL.encode()).hexdigest()):
        guard(tmp_path, failure, (MIGRATION, MANIFEST), {MIGRATION: SQL})
    guard(tmp_path, failure, (MIGRATION, MANIFEST), {MIGRATION: SQL, MANIFEST: CHECKSUM})


def test_manifest_outside_scope_cannot_be_silently_left_stale(tmp_path):
    put(tmp_path, MANIFEST, "")
    with pytest.raises(RepairCompletenessError, match="SHA256SUMS"):
        guard(tmp_path, f"Missing file: {MIGRATION}", (MIGRATION,), {MIGRATION: SQL})


def test_bounded_evidence_ignores_path_mentions_and_accepts_timestamped_missing_list():
    paths = ("docs/A.md", "src/B.py", "tests/C.py")
    failure = "\n".join([
        f"Read {paths[2]} for context; not a failure",
        "2026-10-05T00:00:00.000Z Error: Feature missing mandatory source artifacts:",
        f"2026-10-05T00:00:00.001Z {paths[0]}",
        f"2026-10-05T00:00:00.002Z {paths[1]}",
        f"2026-10-05T00:00:00.003Z     at file:///tmp/{paths[2]}:33:9",
    ])
    assert collect_repair_evidence(failure, paths).missing_paths == paths[:2]


def test_structured_evidence_is_allowlisted_and_not_validator_authority():
    failure = 'DISPATCHER_REPAIR_EVIDENCE={"missing_paths":["docs/A.md","outside.txt"]}'
    assert collect_repair_evidence(failure, ("docs/A.md",)).missing_paths == ("docs/A.md",)
    with pytest.raises(RepairCompletenessError, match="unsupported"):
        collect_repair_evidence('DISPATCHER_REPAIR_EVIDENCE={"validator_defect":true}', ())


def test_symlink_evidence_cannot_read_outside_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (repo / "docs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RepairCompletenessError, match="escapes"):
        guard(repo, "Missing file: docs/A.md", ("docs/A.md",), {"docs/A.md": "x"})


def test_trusted_edit_cannot_write_through_out_of_checkout_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (repo / "docs").symlink_to(outside, target_is_directory=True)
    order = SimpleNamespace(allowed_paths=("docs/A.md",), required_changed_paths=())
    edit = patch.ProductEdit("docs/A.md", "create", "", "content\n")
    with pytest.raises(patch.ProductPilotError, match="escapes"):
        patch.apply_edit_plan(repo, (edit,), order, require_required_paths=False)
    assert list(outside.iterdir()) == []


def test_unchanged_crlf_sql_uses_actual_bytes_in_manifest(tmp_path):
    raw = b"SELECT 2;\r\n"
    put(tmp_path, MIGRATION, "")
    (tmp_path / MIGRATION).write_bytes(raw)
    exact = hashlib.sha256(raw).hexdigest() + "  002_feature.sql\n"
    guard(tmp_path, CHECKSUM_FAILURE, (MIGRATION, MANIFEST), {MANIFEST: exact})


@pytest.mark.parametrize("complete_replacement", [True, False])
def test_partial_plan_regeneration_stays_same_repair_without_commit(
    tmp_path, monkeypatch, complete_replacement
):
    repo = tmp_path / "product"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              text=True, capture_output=True).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "synthetic@example.invalid")
    put(repo, "AGENTS.md", "synthetic scope\n")
    put(repo, "required.txt", "base\n")
    git("add", ".")
    git("commit", "-qm", "source base")
    base = git("rev-parse", "HEAD")
    put(repo, "required.txt", "task\n")
    git("add", ".")
    git("commit", "-qm", "initial task")
    head = git("rev-parse", "HEAD")
    work = tmp_path / "work.json"
    paths = ("docs/A.md", "src/B.py", "tests/C.py")
    work.write_text(json.dumps({
        "schema_version": 1, "task_id": "CLASSMO-REPAIR-001", "project": "CLASSMO",
        "repository": "owner/product", "target_branch": "main", "source_base_sha": base,
        "title": "Synthetic complete repair", "integration_authorized": True,
        "validation_workflow_path": ".github/workflows/validate.yml",
        "required_reads": ["AGENTS.md"], "allowed_paths": ["required.txt", *paths],
        "required_changed_paths": ["required.txt"], "scope": ["Complete synthetic artifacts"],
        "exclusions": ["No provider work"], "completion_conditions": ["Validation PASS"],
    }))
    prompts = []

    class Codex:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def account(self, **kwargs):
            return SimpleNamespace(account=object())

        def thread_start(self, **kwargs):
            assert kwargs["sandbox"] == patch.Sandbox.read_only
            return self

        def run(self, prompt, **kwargs):
            assert git("rev-parse", "HEAD") == head
            assert git("status", "--porcelain") == ""
            assert not any((repo / path).exists() for path in paths)
            prompts.append(prompt)
            selected = paths if complete_replacement and len(prompts) == 2 else paths[:1]
            return SimpleNamespace(status="completed", error=None, final_response=json.dumps({
                "edits": [{"path": path, "operation": "create", "old_text": "",
                           "new_text": "complete\n"} for path in selected],
                "summary": "synthetic artifacts",
            }))

    monkeypatch.setattr(patch, "Codex", Codex)
    failure = "Missing paths:\n" + "\n".join(paths)
    if complete_replacement:
        _, changed = patch.generate_and_apply_product_repair(
            repo, work, "a" * 40, tmp_path / "auth", failure
        )
        assert set(changed) == set(paths)
        assert len(prompts) == 2
        assert "src/B.py" in prompts[1] and "complete REPLACEMENT" in prompts[1]
        assert "smallest COMPLETE repair" in prompts[0]
        assert "required.txt" in patch.validate_branch_scope(repo, patch.load_work_order(work))
    else:
        with pytest.raises(patch.ProductPilotError, match="Incomplete repair"):
            patch.generate_and_apply_product_repair(
                repo, work, "a" * 40, tmp_path / "auth", failure
            )
        assert len(prompts) == 1 + patch.MAX_REPAIR_PLAN_REGENERATION_ATTEMPTS == 3
        assert git("status", "--porcelain") == ""
    # Neither a rejected plan nor an accepted edit plan commits; the workflow
    # consumes one of its two repair commits only after this function succeeds.
    assert git("rev-parse", "HEAD") == head
    assert git("rev-list", "--count", "HEAD") == "2"
