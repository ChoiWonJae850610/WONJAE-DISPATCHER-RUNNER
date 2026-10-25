"""Synthetic non-provider checks for the Owner media read-only preflight."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
module = importlib.import_module("wafl_development_media_readonly_preflight")


def test_native_webp_preflight_does_not_require_images_entitlement_metadata():
    assert "WAFL_R2_IMAGES_ENTITLEMENT_APPROVED" not in module.REQUIRED_INPUT_NAMES
    assert "WAFL_PDF_BROWSER_ENTITLEMENT_APPROVED" in module.REQUIRED_INPUT_NAMES
    assert "WAFL_MEDIA_DEV_BILLING_APPROVED" in module.REQUIRED_INPUT_NAMES



def test_rejects_untrusted_workflow_before_credential_use(monkeypatch):
    for name in (
        "GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_EVENT_NAME",
        "OWNER_GITHUB_TOKEN", "CLOUDFLARE_API_TOKEN",
        "WAFL_CLOUDFLARE_ACCOUNT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(module.BootstrapBlocked, match="TRUSTED"):
        module.protected_runtime()


def test_requires_existing_account_and_pat_without_new_approval_secrets(monkeypatch):
    values = {
        "GITHUB_REPOSITORY": module.RUNNER_REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "OWNER_GITHUB_TOKEN": "synthetic-pat",
        "CLOUDFLARE_API_TOKEN": "synthetic-cloudflare",
        "WAFL_CLOUDFLARE_ACCOUNT_ID": "a" * 32,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    result = module.protected_runtime()
    assert set(result) == {
        "OWNER_GITHUB_TOKEN",
        "CLOUDFLARE_API_TOKEN",
        "WAFL_CLOUDFLARE_ACCOUNT_ID",
    }
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN")
    with pytest.raises(module.BootstrapBlocked, match="MISSING"):
        module.protected_runtime()


def test_gh_reads_only_secret_metadata_with_trusted_pat(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="WAFL_CLOUDFLARE_ACCOUNT_ID\nCLOUDFLARE_API_TOKEN\n",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    names = module.secret_metadata_read("synthetic-pat")
    assert names == {"WAFL_CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"}
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:4] == ["gh", "api", "-X", "GET"]
    assert "set" not in args and "POST" not in args
    assert "synthetic-pat" not in " ".join(args)
    assert kwargs["env"]["GH_TOKEN"] == "synthetic-pat"


def test_gh_read_denied_fails_before_any_claim(monkeypatch):
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *_a, **_kw: SimpleNamespace(returncode=1, stdout=""),
    )
    with pytest.raises(module.BootstrapBlocked, match="SECRET_READ_DENIED"):
        module.secret_metadata_read("synthetic-pat")


def test_missing_future_approvals_do_not_block_useful_readonly_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "source_identity", lambda *_a: None)
    monkeypatch.setattr(module, "protected_runtime", lambda: {
        "OWNER_GITHUB_TOKEN": "synthetic-pat",
        "CLOUDFLARE_API_TOKEN": "synthetic-cloudflare",
        "WAFL_CLOUDFLARE_ACCOUNT_ID": "a" * 32,
    })
    monkeypatch.setattr(module, "secret_metadata_read", lambda _token: {
        "OWNER_GITHUB_TOKEN", "CLOUDFLARE_API_TOKEN", "WAFL_CLOUDFLARE_ACCOUNT_ID",
    })
    received = []
    monkeypatch.setattr(
        module, "cloudflare_inventory",
        lambda conf: received.append(conf) or "synthetic-subdomain",
    )
    report = tmp_path / "summary"
    module.inspect(tmp_path, "b" * 40, report)
    data = report.read_text()
    assert "MISSING_NAMES" in data
    assert "PASS_NEW_NAMES_ABSENT" in data
    assert "NOT_VERIFIED" in data
    assert "NOT_AUTHORIZED" in data
    assert "synthetic-pat" not in data
    assert "synthetic-cloudflare" not in data
    assert received == [{
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "CLOUDFLARE_API_TOKEN": "synthetic-cloudflare",
    }]


def test_collision_stops_without_resource_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "source_identity", lambda *_a: None)
    monkeypatch.setattr(module, "protected_runtime", lambda: {
        "OWNER_GITHUB_TOKEN": "synthetic-pat",
        "CLOUDFLARE_API_TOKEN": "synthetic-cloudflare",
        "WAFL_CLOUDFLARE_ACCOUNT_ID": "a" * 32,
    })
    monkeypatch.setattr(
        module, "secret_metadata_read",
        lambda _token: set(module.SECRET_NAMES),
    )
    monkeypatch.setattr(module, "cloudflare_inventory", lambda _env: "unused")
    output = tmp_path / "report"
    with pytest.raises(module.BootstrapBlocked, match="NEW_SECRET_NAME_COLLISION"):
        module.inspect(tmp_path, "b" * 40, output)
    assert "BLOCKED" in output.read_text()


def test_cloudflare_denied_is_reported_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "source_identity", lambda *_a: None)
    monkeypatch.setattr(module, "protected_runtime", lambda: {
        "OWNER_GITHUB_TOKEN": "synthetic-pat",
        "CLOUDFLARE_API_TOKEN": "synthetic-cloudflare",
        "WAFL_CLOUDFLARE_ACCOUNT_ID": "a" * 32,
    })
    monkeypatch.setattr(module, "secret_metadata_read", lambda _t: set())
    def denied(_env):
        raise module.BootstrapBlocked("BOOTSTRAP_PROVIDER_READBACK_DENIED")
    monkeypatch.setattr(module, "cloudflare_inventory", denied)
    report = tmp_path / "report"
    with pytest.raises(module.BootstrapBlocked, match="CLOUDFLARE_UNVERIFIED"):
        module.inspect(tmp_path, "b" * 40, report)
    content = report.read_text()
    assert "BOOTSTRAP_PROVIDER_READBACK_DENIED" in content
    assert "PASS_NEW_NAMES_ABSENT" not in content
    assert "synthetic-cloudflare" not in content


def test_preflight_source_has_no_create_or_secret_write_commands():
    source = (ROOT / "scripts" / "wafl_development_media_readonly_preflight.py").read_text()
    assert '"GET"' in source
    assert '"POST"' not in source
    assert "gh secret set" not in source
    assert '["wrangler", "r2", "bucket", "create"' not in source
    assert "wrangler deploy" not in source
