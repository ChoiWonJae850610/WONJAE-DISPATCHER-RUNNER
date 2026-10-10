"""Offline fail-closed checks for an already-attached recovery-doc PR.

No GitHub credentials, product writes, runner dispatch, or provider access.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "read_only_recovery_reconciliation", SCRIPTS / "reconcile_recovery_docs.py",
)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
from attach_recovery_docs import wait_attached_pr_head  # noqa: E402

SRC = "a" * 40
DOC = "b" * 40
ATTACHED = "c" * 40
BASE = "d" * 40
CONTROL = "e" * 40
RUN = 38038579147
ATTACH_RUN = 38044276553
REPO = "ChoiWonJae850610/WAFL"
STATE = ".wonjae/execution-state.yaml"
ROADMAP = "docs/current/11-ROADMAP.md"
SCHEMA = ".github/workflows/wafl-cloud-validation.yml"
SHA_RE = "^[0-9a-f]{40}$"
TICK = chr(96)


def inputs() -> Namespace:
    return Namespace(
        registry="unused", project="WAFL",
        source_pr=66, source_head=SRC, attached_head=ATTACHED,
        docs_pr=67, docs_head=DOC, failed_run_id=RUN,
        failed_attach_run_id=ATTACH_RUN, base=BASE, control_sha=CONTROL,
    )


def route() -> dict:
    return {
        "repo": REPO, "branch": "cloud-dev-v1", "state_path": STATE,
        "workflow": SCHEMA, "workflow_name": "WAFL Cloud Validation",
    }


def old_state() -> dict:
    return {
        "schema_version": 1, "project": "WAFL",
        "next_action": {
            "type": "SOURCE_READY", "title": "Domain source 005",
            "source_task_id": "WAFL-CLOUD-DOMAIN-005",
            "source_scope": [
                "approved forward-only 005 schema",
                "required offline invariant check",
            ],
        },
        "after_source_success": {
            "type": "SOURCE_READY", "title": "RPC source 006",
            "source_task_id": "WAFL-CLOUD-RPC-006", "source_scope": ["only RPC code"],
        },
        "source_success_queue": [{
            "type": "DECISION_REQUIRED", "title": "Provider G1",
            "source_task_id": None, "source_scope": [],
        }],
    }


def doc_state() -> dict:
    result = copy.deepcopy(old_state())
    result["next_action"]["source_scope"].append(
        f"Owner-approved PR #66 recovery exception {SRC} Runner {RUN} "
        "allow only tests/wafl-cloud-auth-004-contract.mjs directory-list assertion "
        "without changing applied migration hashes or security tests."
    )
    return result


def sha_blob(content: bytes) -> str:
    preamble = f"blob {len(content)}".encode() + bytes((0,))
    return hashlib.sha1(preamble + content).hexdigest()


def encoded(content: bytes) -> dict:
    return {
        "type": "file", "size": len(content), "encoding": "base64",
        "sha": sha_blob(content), "content": base64.b64encode(content).decode(),
    }


def fixture() -> dict:
    original_state = yaml.safe_dump(old_state(), allow_unicode=True).encode()
    final_state = yaml.safe_dump(doc_state(), allow_unicode=True).encode()
    original_roadmap = b"# Current Roadmap\n"
    final_roadmap = original_roadmap + b"Approved 004 inventory-only change.\n"
    blobs = {
        (BASE, STATE): encoded(original_state),
        (SRC, STATE): encoded(original_state),
        (DOC, STATE): encoded(final_state),
        (ATTACHED, STATE): encoded(final_state),
        (BASE, ROADMAP): encoded(original_roadmap),
        (SRC, ROADMAP): encoded(original_roadmap),
        (DOC, ROADMAP): encoded(final_roadmap),
        (ATTACHED, ROADMAP): encoded(final_roadmap),
    }
    source_pr = {
        "number": 66, "state": "open", "merged_at": None,
        "draft": False, "changed_files": 6,
        "user": {"login": "ChoiWonJae850610"},
        "head": {
            "sha": ATTACHED, "ref": "direct/WAFL-38038579147",
            "repo": {"full_name": REPO},
        },
        "base": {
            "ref": "cloud-dev-v1", "sha": BASE,
            "repo": {"full_name": REPO},
        },
        "body": (
            f"- project: {TICK}WAFL{TICK}\n"
            f"- source_base_sha: {TICK}{BASE}{TICK}\n"
            f"- runner_run_id: {TICK}{RUN}{TICK}\n"
        ),
    }
    docs_pr = {
        "number": 67, "state": "open", "merged_at": None,
        "draft": False, "mergeable": True, "changed_files": 2,
        "user": {"login": "ChoiWonJae850610"},
        "head": {
            "sha": DOC, "ref": "docs/wafl-recovery",
            "repo": {"full_name": REPO},
        },
        "base": {
            "ref": "cloud-dev-v1", "sha": BASE,
            "repo": {"full_name": REPO},
        },
    }
    commit = {
        "sha": ATTACHED, "parents": [{"sha": SRC}],
        "files": [
            {"filename": STATE, "status": "modified"},
            {"filename": ROADMAP, "status": "modified"},
        ],
        "commit": {
            "message": "docs: attach recovery PR #67 to source PR #66",
            "author": {
                "name": "WONJAE trusted recovery docs",
                "email": "trusted-recovery@users.noreply.github.com",
            },
            "committer": {
                "name": "WONJAE trusted recovery docs",
                "email": "trusted-recovery@users.noreply.github.com",
            },
        },
    }
    files = [
        {"filename": STATE, "status": "modified", "changes": 17},
        {"filename": ROADMAP, "status": "modified", "changes": 14},
    ]
    failed_run = {
        "id": ATTACH_RUN, "status": "completed", "conclusion": "failure",
        "event": "workflow_dispatch", "head_branch": "main",
        "path": ".github/workflows/source-pr-recovery-docs.yml",
        "display_title": gate.exact_attach_run_name(inputs()),
    }
    ci = {
        DOC: [{
            "id": 38043958630, "status": "completed", "conclusion": "success",
            "head_sha": DOC, "event": "pull_request",
            "path": SCHEMA, "name": "WAFL Cloud Validation",
        }],
        ATTACHED: [{
            "id": 38044309054, "status": "completed", "conclusion": "success",
            "head_sha": ATTACHED, "event": "pull_request",
            "path": SCHEMA, "name": "WAFL Cloud Validation",
        }],
    }
    return {
        "source": source_pr, "docs": docs_pr, "commit": commit,
        "files": files, "run": failed_run, "blobs": blobs, "ci": ci,
        "open_prs": [
            {"number": 66, "head": {"ref": "direct/WAFL-38038579147"}},
            {"number": 67, "head": {"ref": "docs/wafl-recovery"}},
        ],
        "base": BASE, "manual_ok": True,
    }


def perform(monkeypatch, tmp_path, data, capsys=None) -> int:
    registry = tmp_path / "PROJECTS.yaml"
    registry.write_text("projects: {}\n")
    params = inputs()
    params.registry = str(registry)
    monkeypatch.setenv("GH_TOKEN", "synthetic-read-only-test")
    monkeypatch.setattr(gate, "route_from_registry", lambda *_: route())
    monkeypatch.setattr(gate, "branch_sha", lambda *_: data["base"])
    monkeypatch.setattr(gate, "verify_manual_gate", lambda *_: (
        None if data["manual_ok"] else (_ for _ in ()).throw(
            gate.DocumentMergeError("missing original manual gate")
        )
    ))
    monkeypatch.setattr(gate, "request_runner_json", lambda *_: data["run"])
    monkeypatch.setattr(
        gate, "canonical_runs", lambda _repo, _workflow, sha, _event:
        {"workflow_runs": data["ci"][sha]},
    )

    def request(args):
        target = args[0]
        if target.endswith("/pulls/66"):
            return data["source"]
        if target.endswith("/pulls/67"):
            return data["docs"]
        if target.endswith("/pulls/67/files?per_page=100"):
            return data["files"]
        if target.endswith(f"/commits/{ATTACHED}"):
            return data["commit"]
        if "/pulls?state=open&base=" in target:
            return data["open_prs"]
        if "/contents/" in target:
            path_and_ref = target.split("/contents/", 1)[1]
            path, sha = path_and_ref.split("?ref=", 1)
            return data["blobs"][(sha, path)]
        raise AssertionError(f"unexpected read: {target}")

    monkeypatch.setattr(gate, "request_json", request)
    # A successful reconciliation must never reach git or any other subprocess.
    monkeypatch.setattr(
        gate.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("reconcile tried to execute a subprocess")
        ),
    )
    return gate.reconcile(params)


def test_read_only_exact_reconciliation_returns_pass_without_any_push(
    monkeypatch, tmp_path, capsys,
):
    assert perform(monkeypatch, tmp_path, fixture(), capsys) == 38044309054
    output = capsys.readouterr().out
    assert "RECOVERY_DOCS_RECONCILED" in output
    assert "attached_head=" + ATTACHED in output
    assert "read_only=true" in output
    assert "resume=SEPARATE_OWNER_ACTION" in output


@pytest.mark.parametrize("mutation", [
    lambda d: d["source"]["head"].update(sha=SRC),
    lambda d: d["source"]["head"].update(ref="direct/WAFL-other"),
    lambda d: d["source"]["base"].update(sha="f" * 40),
    lambda d: d["source"].update(state="closed"),
    lambda d: d["source"].update(draft=True),
    lambda d: d["source"].update(body="lost prior manual run binding"),
    lambda d: d["docs"]["head"].update(sha="f" * 40),
    lambda d: d["docs"].update(mergeable=False),
    lambda d: d["commit"]["parents"][0].update(sha="f" * 40),
    lambda d: d["commit"]["parents"].append({"sha": BASE}),
    lambda d: d["commit"]["files"].append({
        "filename": "tests/unsafe.mjs", "status": "modified"
    }),
    lambda d: d["commit"]["files"][0].update(status="removed"),
    lambda d: d["commit"]["commit"]["author"].update(name="Unknown"),
    lambda d: d["commit"]["commit"].update(message="manual replacement"),
    lambda d: d["run"].update(conclusion="success"),
    lambda d: d["run"].update(display_title="wrong run"),
    lambda d: d["open_prs"].append({
        "number": 68, "head": {"ref": "direct/WAFL-other"}
    }),
    lambda d: d.update(base="f" * 40),
    lambda d: d.update(manual_ok=False),
    lambda d: d["blobs"].update({
        (ATTACHED, ROADMAP): encoded(b"not exact docs branch content")
    }),
    lambda d: d["blobs"].update({
        (SRC, STATE): encoded(b"already changed protected source state")
    }),
    lambda d: d["ci"][ATTACHED][0].update(conclusion="failure"),
    lambda d: d["ci"][DOC][0].update(conclusion="failure"),
    lambda d: d["ci"][ATTACHED][0].update(event="push"),
    lambda d: d["ci"][ATTACHED].append({
        **d["ci"][ATTACHED][0], "id": 38044309055, "conclusion": "failure",
    }),
])
def test_reconciliation_rejects_wrong_provenance_scope_ci_or_writer(
    monkeypatch, tmp_path, mutation,
):
    data = fixture()
    mutation(data)
    with pytest.raises(gate.DocumentMergeError):
        perform(monkeypatch, tmp_path, data)


def test_scope_modification_does_not_advance_006_or_provider_gate():
    old = old_state()
    next_stage = doc_state()
    next_stage["after_source_success"]["source_task_id"] = "different"
    with pytest.raises(gate.DocumentMergeError):
        gate.check_scope(
            yaml.safe_dump(old), yaml.safe_dump(next_stage), 66, SRC, RUN,
        )


def test_published_pr_readback_waits_for_expected_sha(monkeypatch):
    from attach_recovery_docs import request_json as _original  # noqa: F401
    import attach_recovery_docs as script

    seen = []
    old = {"number": 66, "state": "open", "merged_at": None,
           "base": {"sha": BASE, "ref": "cloud-dev-v1"},
           "head": {"sha": SRC, "ref": "direct/WAFL-38038579147"}}
    new = copy.deepcopy(old)
    new["head"]["sha"] = ATTACHED
    values = [old, old, new]
    monkeypatch.setattr(script, "request_json", lambda _: seen.append("read")
                        or values.pop(0))
    monkeypatch.setattr(script.time, "sleep", lambda _: None)
    wait_attached_pr_head(REPO, route(), inputs(), ATTACHED,
                          "direct/WAFL-38038579147", attempts=4, interval=0)
    assert len(seen) == 3
    assert not values


def test_published_pr_readback_rejects_competing_untrusted_head(monkeypatch):
    import attach_recovery_docs as script

    old = {"state": "open", "merged_at": None,
           "base": {"sha": BASE, "ref": "cloud-dev-v1"},
           "head": {"sha": "f" * 40, "ref": "direct/WAFL-38038579147"}}
    monkeypatch.setattr(script, "request_json", lambda _: old)
    with pytest.raises(gate.DocumentMergeError, match="unapproved SHA"):
        wait_attached_pr_head(REPO, route(), inputs(), ATTACHED,
                              "direct/WAFL-38038579147", attempts=2, interval=0)


def test_owner_workflow_has_distinct_reconcile_gate_without_dispatch_of_source():
    core = Path(".github/workflows/source-pr-recovery-docs-core.yml").read_text()
    caller = Path(".github/workflows/source-pr-recovery-docs.yml").read_text()
    assert "reconcile_recovery_docs.py" in core
    assert "github.actor == github.repository_owner" in core
    assert "group: direct-worker-" in core
    assert "product_write_token" in core
    assert "CONTROL_READ_TOKEN" in caller
    assert "attached_head:" in caller and "failed_attach_run_id:" in caller
    assert "reconcile" in caller
    assert "direct-worker.yml/dispatches" not in core
    assert "wrangler deploy" not in core
    assert "git push" not in core
