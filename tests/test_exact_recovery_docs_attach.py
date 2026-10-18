"""Exact two-file manual-gate attachment guards; never call product/provider APIs."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "attach_recovery_docs.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("exact_recovery_attach", SCRIPT)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

SOURCE = "a" * 40
BASE = "b" * 40
DOCS = "c" * 40
RUN = 38038579147


def route():
    return {"repo": "ChoiWonJae850610/WAFL", "branch": "cloud-dev-v1"}


def source():
    tick = chr(96)
    return {
        "number": 66, "state": "open", "merged_at": None, "draft": False,
        "head": {"sha": SOURCE, "ref": "direct/WAFL-38038579147",
                 "repo": {"full_name": route()["repo"]}},
        "base": {"sha": BASE, "ref": "cloud-dev-v1",
                 "repo": {"full_name": route()["repo"]}},
        "body": (
            "## Direct Worker\n\n"
            f"- project: {tick}WAFL{tick}\n"
            f"- source_base_sha: {tick}{BASE}{tick}\n"
            f"- runner_run_id: {tick}{RUN}{tick}\n"
        ),
    }


def states():
    original = {
        "schema_version": 1, "project": "WAFL",
        "next_action": {
            "type": "SOURCE_READY", "title": "Domain 005",
            "source_task_id": "WAFL-CLOUD-DOMAIN-005",
            "owner_action": "Apply only offline source",
            "source_scope": [
                "new approved 005 source SQL",
                "tests/wafl-cloud-domain-005-contract.mjs and 50 invariant readback",
            ],
        },
        "after_source_success": {
            "type": "SOURCE_READY", "source_task_id": "WAFL-CLOUD-RPC-006",
            "title": "RPC source 006", "source_scope": ["offline RPC tests"],
        },
        "source_success_queue": [{
            "type": "DECISION_REQUIRED", "title": "G1 provider gate",
            "source_scope": [],
        }],
    }
    proposed = yaml.safe_load(yaml.safe_dump(original))
    proposed["next_action"]["source_scope"].append(
        f"Owner-approved PR #66 recovery exception only (original head {SOURCE}, "
        f"Runner {RUN}): allow only tests/wafl-cloud-auth-004-contract.mjs "
        "migration directory assertion; retain 004 security and historic hashes."
    )
    return original, proposed


def check(old, proposed):
    guard.check_scope(
        yaml.safe_dump(old), yaml.safe_dump(proposed), 66, SOURCE, RUN,
    )


def test_source_pr_requires_exact_open_run_and_branch_identity():
    assert guard.checked_source_pr(source(), route(), 66, SOURCE, BASE, RUN) == (
        "direct/WAFL-38038579147"
    )


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(number=67),
    lambda p: p.update(state="closed"),
    lambda p: p.update(draft=True),
    lambda p: p["head"].update(sha=DOCS),
    lambda p: p["head"].update(ref="docs/recovery"),
    lambda p: p["head"].update(ref="direct/../bad"),
    lambda p: p["head"].update(repo={"full_name": "fork/repo"}),
    lambda p: p["base"].update(sha=DOCS),
    lambda p: p.update(body=p["body"].replace(str(RUN), "999")),
    lambda p: p.update(body=p["body"].replace("WAFL", "ESC")),
])
def test_source_pr_rejects_mutated_identity(mutate):
    wrong = source()
    mutate(wrong)
    with pytest.raises(guard.DocumentMergeError):
        guard.checked_source_pr(wrong, route(), 66, SOURCE, BASE, RUN)


def test_only_one_exact_004_test_exception_is_added():
    old, proposed = states()
    check(old, proposed)


@pytest.mark.parametrize("change", [
    lambda p: p["next_action"].update(source_task_id="WAFL-CLOUD-RPC-006"),
    lambda p: p["next_action"].update(type="PROVIDER_GATE"),
    lambda p: p["next_action"].update(owner_action="Delete migration"),
    lambda p: p["after_source_success"].update(type="NONE"),
    lambda p: p["source_success_queue"].clear(),
    lambda p: p["next_action"]["source_scope"].pop(0),
    lambda p: p["next_action"]["source_scope"].append("unapproved other"),
    lambda p: p["next_action"]["source_scope"][-1].replace(str(RUN), "999"),
    lambda p: p["next_action"]["source_scope"][-1].replace(SOURCE, DOCS),
    lambda p: p["next_action"]["source_scope"][-1].replace("PR #66", "PR #67"),
    lambda p: p["next_action"]["source_scope"][-1].replace(
        "tests/wafl-cloud-auth-004-contract.mjs", "docs/anything.md"
    ),
    lambda p: p["next_action"]["source_scope"][-1].replace(
        "tests/wafl-cloud-auth-004-contract.mjs",
        "tests/wafl-cloud-auth-004-contract.mjs "
        "tests/other-security-bypass.mjs"
    ),
])
def test_recovery_fails_closed_on_broadened_or_stale_scope(change):
    old, proposed = states()
    scope = proposed["next_action"]["source_scope"]
    outcome = change(proposed)
    if isinstance(outcome, str):
        scope[-1] = outcome
    with pytest.raises(guard.DocumentMergeError):
        check(old, proposed)


def test_input_validation_rejects_invalid_shas():
    for sha in ("", "main", "0" * 39, "../malicious", "A" * 40):
        with pytest.raises(guard.DocumentMergeError):
            guard.check_sha(sha, "head")


def test_owner_workflows_are_project_scoped_and_non_deploying():
    root = Path(".github/workflows")
    caller = (root / "source-pr-recovery-docs.yml").read_text(encoding="utf-8")
    core = (root / "source-pr-recovery-docs-core.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in caller
    assert "github.actor == github.repository_owner" in caller
    assert caller.count("secrets.OWNER_GITHUB_TOKEN") >= 8
    for old in ("WAFL_WRITE_TOKEN", "CLASSMO_WRITE_TOKEN", "ESC_WRITE_TOKEN",
                "MUVEL_WRITE_TOKEN", "CONTROL_READ_TOKEN"):
        assert f"secrets.{old}" not in caller
    assert "secrets: inherit" not in caller
    assert "group: direct-worker-" in core
    assert "runs-on: [self-hosted, Linux, X64, direct-worker]" in core
    assert "github.ref == 'refs/heads/main'" in core
    assert "scripts/attach_recovery_docs.py" in core
    for forbidden in ("eas build", "eas update", "wrangler deploy",
                      "/merge", "force-with-lease", "force: true"):
        assert forbidden not in core.lower()
    assert "--docs-head" in core and "--source-head" in core
    assert "--control-sha" in core and "--failed-run-id" in core


def test_exact_one_mobile_typescript_test_scope_is_accepted_without_loosening_old_checks():
    old, proposed = states()
    proposed["project"] = old["project"] = "CLASSMO"
    proposed["next_action"]["source_scope"][-1] = (
        f"Owner-approved PR #66 head {SOURCE} failed run {RUN} only "
        "apps/mobile/test/owner-home-session-polish.test.ts "
        "for exact 016 SHA256SUMS; preserve 001-015 hashes and every negative check"
    )
    check(old, proposed)
    proposed["next_action"]["source_scope"][-1] += (
        " apps/mobile/test/other-unapproved.test.ts"
    )
    with pytest.raises(guard.DocumentMergeError):
        check(old, proposed)


def test_docs_attachment_failed_ci_is_not_source_pass():
    assert guard.attached_failed_source_ci_requires_owner_resume(
        {"status": "completed", "conclusion": "failure"}
    )
    for status, conclusion in (
        ("in_progress", None), ("completed", "success"),
        ("completed", "cancelled"), ("completed", "timed_out"),
    ):
        assert not guard.attached_failed_source_ci_requires_owner_resume(
            {"status": status, "conclusion": conclusion}
        )


def test_second_attachment_preserves_first_scope_and_existing_document():
    base, first = states()
    second = yaml.safe_load(yaml.safe_dump(first))
    second["next_action"]["source_scope"].append(
        f"PR #66 head {SOURCE} failed run {RUN}: implement only "
        "scripts/classmo_editorial_p2_live_test.py and registered source-mode "
        "Runtime proof contract; actual credentials and Provider stay NOT_RUN."
    )
    base_doc = "## Existing canonical\n"
    first_doc = "## First trusted attachment\n" + base_doc
    second_doc = "## Latest bound attachment\n" + first_doc
    guard.check_existing_attachment(
        yaml.safe_dump(base), yaml.safe_dump(first),
        base_doc, first_doc, second_doc,
    )
    guard.check_scope(yaml.safe_dump(first), yaml.safe_dump(second), 66, SOURCE, RUN)


@pytest.mark.parametrize("tamper", [
    "drop_first_scope", "change_task", "remove_old_doc", "change_old_doc",
    "reverse_scope", "too_many_previous", "no_new_document",
])
def test_existing_attachment_must_not_be_overwritten(tamper):
    base, first = states()
    base_doc = "Existing canonical\n"
    first_doc = "Previous trusted docs\n" + base_doc
    new_doc = "Another strictly additive section\n" + first_doc
    if tamper == "drop_first_scope":
        first["next_action"]["source_scope"].pop()
    elif tamper == "change_task":
        first["next_action"]["source_task_id"] = "WRONG"
    elif tamper == "remove_old_doc":
        new_doc = "New without prior docs"
    elif tamper == "change_old_doc":
        first_doc = "Previous trusted docs\nWrong existing content\n"
    elif tamper == "reverse_scope":
        first["next_action"]["source_scope"].reverse()
    elif tamper == "too_many_previous":
        first["next_action"]["source_scope"].extend(
            ["PR #66 repeated scope"] * 4
        )
    elif tamper == "no_new_document":
        new_doc = first_doc
    with pytest.raises(guard.DocumentMergeError):
        guard.check_existing_attachment(
            yaml.safe_dump(base), yaml.safe_dump(first),
            base_doc, first_doc, new_doc,
        )


def test_prior_provenance_only_accepts_trusted_two_file_attachment(monkeypatch):
    paths = [".wonjae/execution-state.yaml", "docs/NEXT_WORK.md"]

    def fake_git(_workspace, *args):
        if args[:2] == ("log", "--format=%H"):
            return "d" * 40
        if args[:3] == ("show", "-s", "--format=%s"):
            return "docs: attach recovery PR #150 to source PR #149"
        if args[:3] == ("show", "-s", "--format=%ae"):
            return "trusted-recovery@users.noreply.github.com"
        if args[:3] == ("diff-tree", "--no-commit-id", "--name-only"):
            return "\n".join(paths)
        raise AssertionError(args)

    monkeypatch.setattr(guard, "git", fake_git)
    guard.check_prior_attachment_commits(Path("."), BASE, SOURCE, paths)

    def forged_git(workspace, *args):
        if args[:3] == ("show", "-s", "--format=%ae"):
            return "codex@untrusted.invalid"
        return fake_git(workspace, *args)

    monkeypatch.setattr(guard, "git", forged_git)
    with pytest.raises(guard.DocumentMergeError, match="provenance"):
        guard.check_prior_attachment_commits(Path("."), BASE, SOURCE, paths)


def test_later_manual_resume_keeps_initial_pr_identity_without_requiring_same_run_id():
    # An original PR created by next is not rewritten on resume. The newest
    # MANUAL_REQUIRED run+SHA is independently checked on the same PR.
    first = source()
    first["head"]["sha"] = DOCS
    assert guard.checked_source_pr(first, route(), 66, DOCS, BASE, RUN + 100) == (
        "direct/WAFL-38038579147"
    )
    with pytest.raises(guard.DocumentMergeError):
        guard.checked_source_pr(first, route(), 66, DOCS, BASE, RUN - 1)


def test_original_pr_branch_and_immutable_body_must_agree_on_run_identity():
    p = source()
    p["head"]["ref"] = "direct/WAFL-39000000001"
    with pytest.raises(guard.DocumentMergeError):
        guard.checked_source_pr(p, route(), 66, SOURCE, BASE, RUN + 1)
    p = source()
    p["body"] = p["body"].replace("runner_run_id:", "current_resume_run_id:")
    with pytest.raises(guard.DocumentMergeError):
        guard.checked_source_pr(p, route(), 66, SOURCE, BASE, RUN + 1)
