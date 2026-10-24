"""Offline gate tests. No Cloudflare, GitHub Secrets, or provider calls are made."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wafl_development_media_bootstrap",
    ROOT / "scripts/wafl_development_media_bootstrap.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_static_owner_scope_exact_and_nonlegacy():
    assert module.BUCKET == "wafl-media-dev"
    assert module.IMAGE_WORKER == "wafl-r2-dev"
    assert module.PDF_WORKER == "wafl-pdf-dev"
    assert not ({module.BUCKET, module.IMAGE_WORKER, module.PDF_WORKER}
                & module.LEGACY_NAMES)
    assert "WAFL_R2_WORKER_UPLOAD_SECRET" in module.SECRET_NAMES
    assert "WAFL_PDF_GENERATOR_TOKEN" in module.SECRET_NAMES


def test_absent_owner_credential_and_approval_fail_before_network(monkeypatch):
    for key in (
        "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "OWNER_GITHUB_TOKEN",
        "WAFL_R2_IMAGES_ENTITLEMENT_APPROVED",
        "WAFL_PDF_BROWSER_ENTITLEMENT_APPROVED", "WAFL_MEDIA_DEV_BILLING_APPROVED",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(module.BootstrapBlocked, match="MISSING"):
        module.safe_configuration()


def test_owner_pinned_workflow_and_entitlement_gate(monkeypatch):
    values = {
        "CLOUDFLARE_API_TOKEN": "redacted",
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "OWNER_GITHUB_TOKEN": "redacted",
        "WAFL_R2_IMAGES_ENTITLEMENT_APPROVED": "yes",
        "WAFL_PDF_BROWSER_ENTITLEMENT_APPROVED": "yes",
        "WAFL_MEDIA_DEV_BILLING_APPROVED": "yes",
        "GITHUB_REPOSITORY": module.RUNNER_REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    assert module.safe_configuration()["WAFL_MEDIA_DEV_BILLING_APPROVED"] == "yes"
    monkeypatch.setenv("WAFL_MEDIA_DEV_BILLING_APPROVED", "no")
    with pytest.raises(module.BootstrapBlocked, match="BILLING"):
        module.safe_configuration()
    monkeypatch.setenv("WAFL_MEDIA_DEV_BILLING_APPROVED", "yes")
    monkeypatch.setenv("GITHUB_REF", "refs/pull/335/merge")
    with pytest.raises(module.BootstrapBlocked, match="TRUSTED"):
        module.safe_configuration()


def test_existing_github_secret_blocks_without_writes(monkeypatch):
    def fake_run(args, **kwargs):
        assert args[:2] == ["gh", "api"]
        return SimpleNamespace(returncode=0, stdout="WAFL_PDF_GENERATOR_TOKEN\n")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(module.BootstrapBlocked, match="EXISTING_GITHUB_SECRET"):
        module.github_secret_inventory({"OWNER_GITHUB_TOKEN": "redacted"})


def test_existing_r2_worker_blocks_pre_resource_creation(monkeypatch):
    calls = []

    def fake_api(url, token):
        calls.append(url)
        return [{"id": module.IMAGE_WORKER}]
    monkeypatch.setattr(module, "api", fake_api)
    with pytest.raises(module.BootstrapBlocked, match="WORKER_ALREADY_EXISTS"):
        module.cloudflare_inventory({
            "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
            "CLOUDFLARE_API_TOKEN": "redacted",
        })
    assert len(calls) == 1


def test_existing_bucket_blocks_pre_resource_creation(monkeypatch):
    def fake_api(url, token):
        if url.endswith("/workers/scripts"):
            return []
        return {"buckets": [{"name": module.BUCKET}]}
    monkeypatch.setattr(module, "api", fake_api)
    with pytest.raises(module.BootstrapBlocked, match="BUCKET_ALREADY_EXISTS"):
        module.cloudflare_inventory({
            "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
            "CLOUDFLARE_API_TOKEN": "redacted",
        })


def test_wrangler_configs_never_use_incumbent_worker_or_bucket(tmp_path):
    repo = tmp_path / "product"
    (repo / "cloudflare/pdf-generator-worker/src").mkdir(parents=True)
    (repo / "cloudflare/r2-upload-worker.js").touch()
    (repo / "cloudflare/pdf-generator-worker/src/index.js").touch()
    r2_cfg, pdf_cfg = tmp_path / "r2.toml", tmp_path / "pdf.toml"
    module.worker_config(r2_cfg, repo, image=True)
    module.worker_config(pdf_cfg, repo, image=False)
    r2, pdf = r2_cfg.read_text(), pdf_cfg.read_text()
    assert 'name = "wafl-r2-dev"' in r2
    assert 'bucket_name = "wafl-media-dev"' in r2
    assert 'binding = "IMAGES"' in r2
    assert 'name = "wafl-pdf-dev"' in pdf
    assert 'binding = "BROWSER"' in pdf
    assert all(s not in r2 + pdf for s in module.LEGACY_NAMES)


def test_no_cloudflare_resources_before_scoped_secret_write(tmp_path, monkeypatch):
    repo = tmp_path / "product"
    (repo / "cloudflare/pdf-generator-worker/src").mkdir(parents=True)
    (repo / "cloudflare/r2-upload-worker.js").touch()
    (repo / "cloudflare/pdf-generator-worker/src/index.js").touch()
    (repo / "cloudflare/pdf-generator-worker/package-lock.json").touch()
    events = []
    monkeypatch.setattr(module, "source_identity", lambda *args: None)
    monkeypatch.setattr(module, "safe_configuration", lambda: {
        "CLOUDFLARE_API_TOKEN": "mock",
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "OWNER_GITHUB_TOKEN": "mock",
    })
    monkeypatch.setattr(module, "cloudflare_inventory", lambda *args: "mock-owner")
    monkeypatch.setattr(module, "github_secret_inventory", lambda *args: None)
    monkeypatch.setattr(module, "readback", lambda *args: events.append("readback"))
    monkeypatch.setattr(
        module, "install_secrets",
        lambda env, values: events.append(("secrets", values.copy())),
    )
    def fake_command(args, **kwargs):
        events.append(tuple(args))
    monkeypatch.setattr(module, "command", fake_command)
    output = tmp_path / "summary"
    module.bootstrap(repo, "a" * 40, output)
    secret_idx = next(i for i, x in enumerate(events) if x[0] == "secrets")
    bucket_idx = next(i for i, x in enumerate(events)
                      if x[:4] == ("wrangler", "r2", "bucket", "create"))
    assert secret_idx < bucket_idx
    assert sum(isinstance(x, tuple) and x[:2] == ("wrangler", "deploy")
               and "--dry-run" in x for x in events) == 2
    assert events[-1] == "readback"
    assert "PASS" in output.read_text()
    assert "peacebypiece" not in output.read_text()


def test_secret_command_uses_stdin_not_arguments(monkeypatch):
    observed = []

    def fake_command(args, **kwargs):
        observed.append((args, kwargs.get("stdin")))
    monkeypatch.setattr(module, "command", fake_command)
    data = {key: "hidden-" + key for key in module.SECRET_NAMES}
    module.install_secrets({"OWNER_GITHUB_TOKEN": "mock"}, data)
    assert [x[0][3] for x in observed] == list(module.SECRET_NAMES)
    for args, value in observed:
        assert value not in " ".join(args)
