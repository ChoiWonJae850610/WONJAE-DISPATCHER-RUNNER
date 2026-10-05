import hashlib
import json
import subprocess
import time
from types import SimpleNamespace

import pytest

from wonjae_dispatcher_runner import product_patch as patch
from wonjae_dispatcher_runner.repair_completeness import (
    RepairCompletenessError,
    check_repair_completeness,
    collect_repair_evidence,
)
from wonjae_dispatcher_runner.repair_timeout import repair_plan_deadline

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


@pytest.mark.parametrize("format", ["comma", "bullet", "plain", "structured"])
def test_real_log_failed_transport_preserves_entire_aggregate(tmp_path, format):
    paths = ("docs/A.md", "db/migrations/002_feature.sql", "tests/C.sql", "tests/D.sql")
    if format == "structured":
        lines = ["DISPATCHER_REPAIR_EVIDENCE=" + json.dumps({"missing_paths": list(paths)})]
    else:
        header = "Error: Migration 002 missing mandatory source artifacts:"
        lines = [header + " " + ", ".join(paths)] if format == "comma" else [header] + [
            ("- " if format == "bullet" else "") + path for path in paths
        ]
    failure = "\n".join(
        "Typecheck, domain tests and export\tValidate repository\t"
        "2026-10-05T07:00:04.2800978Z " + line for line in lines
    )
    assert set(collect_repair_evidence(failure, paths).missing_paths) == set(paths)
    with pytest.raises(RepairCompletenessError, match="tests/C.sql"):
        guard(tmp_path, failure, paths, {paths[0]: "doc only\n"})
    guard(tmp_path, failure, paths, dict.fromkeys(paths, "complete\n"))


@pytest.mark.parametrize("path", ["outside.txt", "../private.txt", "/private.txt",
                                  "docs/*.md", "docs/A.md or docs/B.md"])
def test_explicit_unsafe_ambiguous_or_unscoped_item_fails_closed(tmp_path, path):
    with pytest.raises(RepairCompletenessError, match="unsafe, ambiguous or outside"):
        guard(tmp_path, f"Missing paths: docs/A.md, {path}", ("docs/A.md",),
              {"docs/A.md": "partial\n"})


def test_failure_log_cannot_silently_drop_aggregate_header():
    with pytest.raises(RepairCompletenessError, match="do not truncate"):
        collect_repair_evidence("Missing paths: docs/A.md\n" + "x" * 80_000, ("docs/A.md",))


def test_exact_allowed_path_with_spaces_is_unambiguous(tmp_path):
    path = "docs/Feature Design.md"
    guard(tmp_path, f"Missing paths: {path}", (path,), {path: "complete\n"})


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
    with pytest.raises(RepairCompletenessError, match="outside allowed_paths"):
        collect_repair_evidence(failure, ("docs/A.md",))
    with pytest.raises(RepairCompletenessError, match="unsupported"):
        collect_repair_evidence('DISPATCHER_REPAIR_EVIDENCE={"validator_defect":true}', ())


def test_unscoped_condition_is_not_proof_of_validator_defect(tmp_path):
    put(tmp_path, "docs/A.md", "correct\n")
    with pytest.raises(RepairCompletenessError, match="outside allowed_paths"):
        guard(tmp_path, "Missing paths:\ndocs/A.md\noutside.txt", ("docs/A.md", VALIDATOR),
              {VALIDATOR: "# diagnostic change cannot resolve an unscoped condition\n"})


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


@pytest.mark.parametrize("repair_case", [
    "aggregate", "aggregate_log_failed", "checksum", "exhausted", "timeout_then_complete",
    "timeout_exhausted", "incomplete_timeout_exhausted",
])
def test_partial_plan_regeneration_stays_same_repair_without_commit(
    tmp_path, monkeypatch, repair_case
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
    if repair_case == "checksum":
        put(repo, MANIFEST, "")
    git("add", ".")
    git("commit", "-qm", "source base")
    base = git("rev-parse", "HEAD")
    put(repo, "required.txt", "task\n")
    git("add", ".")
    git("commit", "-qm", "initial task")
    head = git("rev-parse", "HEAD")
    work = tmp_path / "work.json"
    paths = ((MIGRATION, MANIFEST) if repair_case == "checksum"
             else ("docs/A.md", "src/B.py", "tests/C.py"))
    complete_replacement = repair_case in {
        "aggregate", "aggregate_log_failed", "checksum", "timeout_then_complete"
    }
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
    closed_sessions = []
    monkeypatch.setattr(patch, "repair_plan_deadline", lambda: repair_plan_deadline(0.2))

    class Codex:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed_sessions.append(self)

        def account(self, **kwargs):
            return SimpleNamespace(account=object())

        def thread_start(self, **kwargs):
            assert kwargs["sandbox"] == patch.Sandbox.read_only
            return self

        def run(self, prompt, **kwargs):
            assert git("rev-parse", "HEAD") == head
            assert git("status", "--porcelain") == ""
            assert not any((repo / path).exists() for path in paths if path != MANIFEST)
            if repair_case == "checksum":
                assert (repo / MANIFEST).read_text() == ""
            prompts.append(prompt)
            if (repair_case in {"timeout_then_complete", "timeout_exhausted"}
                    and (len(prompts) == 1 or not complete_replacement)):
                time.sleep(1)  # Interrupted by a real wall-clock signal, not a mocked exception.
            if repair_case == "incomplete_timeout_exhausted" and len(prompts) > 1:
                time.sleep(1)
            selected = paths if complete_replacement and len(prompts) == 2 else paths[:1]
            if repair_case == "checksum" and len(prompts) == 2:
                assert json.dumps(SQL) in prompt  # The fresh thread receives exact prior SQL.
                assert CHECKSUM.strip() in prompt  # Trusted checksum for those proposed bytes.
            return SimpleNamespace(status="completed", error=None, final_response=json.dumps({
                "edits": [{"path": path, "operation": "write" if path == MANIFEST else "create",
                           "old_text": "", "new_text": SQL if path == MIGRATION
                           else CHECKSUM if path == MANIFEST else "complete\n"}
                          for path in selected],
                "summary": "synthetic artifacts",
            }))

    monkeypatch.setattr(patch, "Codex", Codex)
    failure = (f"Missing file: {MIGRATION}" if repair_case == "checksum"
               else "Missing paths:\n" + "\n".join(paths))
    if repair_case == "aggregate_log_failed":
        failure = ("Typecheck and export\tValidate repository\t2026-10-05T00:00:00.000Z "
                   "Error: Feature missing mandatory source artifacts: " + ", ".join(paths))
    if complete_replacement:
        _, changed = patch.generate_and_apply_product_repair(
            repo, work, "a" * 40, tmp_path / "auth", failure
        )
        assert set(changed) == set(paths)
        assert len(prompts) == 2
        assert paths[-1] in prompts[1] and "complete REPLACEMENT" in prompts[1]
        assert "smallest COMPLETE repair" in prompts[0]
        assert "required.txt" in patch.validate_branch_scope(repo, patch.load_work_order(work))
    else:
        with pytest.raises(patch.ProductPilotError, match="regeneration exhausted before commit"):
            patch.generate_and_apply_product_repair(
                repo, work, "a" * 40, tmp_path / "auth", failure
            )
        assert len(prompts) == 1 + patch.MAX_REPAIR_PLAN_REGENERATION_ATTEMPTS == 3
        assert git("status", "--porcelain") == ""
    # Neither a rejected plan nor an accepted edit plan commits; the workflow
    # consumes one of its two repair commits only after this function succeeds.
    assert git("rev-parse", "HEAD") == head
    assert git("rev-list", "--count", "HEAD") == "2"
    assert len(closed_sessions) == len(prompts)
    if complete_replacement:
        git("add", ".")
        git("commit", "-qm", "one complete validation repair")
        assert git("rev-list", "--count", "HEAD") == "3"
