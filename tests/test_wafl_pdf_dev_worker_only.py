"""Scoped, mocked WAFL PDF Worker-only Owner gate tests; no provider I/O."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wafl_pdf_dev_worker_only as pdf  # noqa: E402


def config() -> dict[str, str]:
    return {
        "CLOUDFLARE_API_TOKEN": "synthetic-secret",
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "WAFL_PDF_DEV_WORKER_NAME": "wafl-pdf-dev",
        "WAFL_PDF_DEV_WORKER_URL": "https://wafl-pdf-dev.synthetic.workers.dev",
        "WAFLOW_PDF_GENERATOR_TOKEN": "synthetic-bearer",
        "GITHUB_REPOSITORY": pdf.RUNNER,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
    }


def version_metadata(bindings=None):
    entries = bindings if bindings is not None else [
        ("BROWSER", "browser"),
        ("WAFLOW_PDF_GENERATOR_TOKEN", "secret_text"),
        ("WAFLOW_PDF_GENERATOR_TIMEOUT_MS", "plain_text"),
    ]
    return {"resources": {"bindings": [
        {"name": name, "type": kind} for name, kind in entries
    ]}}


def test_exact_environment_only():
    env = config()
    with patch.dict(os.environ, env):
        assert pdf.scoped_environment()["WAFL_PDF_DEV_WORKER_NAME"] == pdf.TARGET
    invalid = [
        ("WAFL_PDF_DEV_WORKER_NAME", "waflow-pdf-generator"),
        ("WAFL_PDF_DEV_WORKER_URL", "https://untrusted.invalid/"),
        ("GITHUB_REF", "refs/heads/test"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("CLOUDFLARE_ACCOUNT_ID", "not-an-account"),
        ("WAFLOW_PDF_GENERATOR_TOKEN", ""),
    ]
    for key, val in invalid:
        with patch.dict(os.environ, {**env, key: val}):
            with pytest.raises(pdf.PdfBlocked):
                pdf.scoped_environment()


@pytest.mark.parametrize("bindings", [
    [("BROWSER", "plain_text"), ("WAFLOW_PDF_GENERATOR_TOKEN", "secret_text")],
    [("BROWSER", "browser")],
    [("BROWSER", "browser"), ("WAFLOW_PDF_GENERATOR_TOKEN", "plain_text")],
    [
        ("BROWSER", "browser"),
        ("WAFLOW_PDF_GENERATOR_TOKEN", "secret_text"),
        ("R2_BUCKET", "r2_bucket"),
    ],
    [("BROWSER", "browser"), ("BROWSER", "browser"), ("WAFLOW_PDF_GENERATOR_TOKEN", "secret_text")],
])
def test_binding_fence(bindings):
    with patch.object(pdf, "command_json", side_effect=[
        {"deployments": [{"versions": [{"version_id": "a" * 32}]}]},
        version_metadata(bindings),
    ]):
        with pytest.raises(pdf.PdfBlocked):
            pdf.deployed_version({})


def test_worker_version_readback_without_provider_effect():
    with patch.object(pdf, "command_json", side_effect=[
        {"deployments": [{"versions": [{"version_id": "a" * 32}]}]},
        version_metadata(),
    ]):
        assert pdf.deployed_version({}) == "a" * 32


def test_dry_run_and_one_worker_deploy_before_one_canary():
    steps = []
    versions = iter(["v1", "v1", "v2"])
    with tempfile.TemporaryDirectory() as path:
        root = Path(path)
        (root / pdf.ENTRY).parent.mkdir(parents=True)
        (root / pdf.ENTRY).write_text("synthetic", encoding="utf-8")
        with patch.dict(os.environ, config()), \
                patch.object(pdf, "source_identity", return_value=root), \
                patch.object(pdf, "deployed_version", side_effect=lambda *_: next(versions)), \
                patch.object(
                    pdf, "run_quiet", side_effect=lambda args, **kwargs: steps.append(args),
                ), \
                patch.object(pdf, "canary") as canary:
            pdf.execute(root, "a" * 40, "b" * 40, "c" * 40, "d" * 40, root / "summary")
            assert [args[0] for args in steps] == ["npm", "wrangler", "wrangler"]
            assert "--dry-run" in steps[1] and "--dry-run" not in steps[2]
            assert "--keep-vars" in steps[1] and "--keep-vars" in steps[2]
            canary.assert_called_once()
            assert "R2, credentials" in (root / "summary").read_text()


def test_safe_stage_parse_and_rejection_of_private_stage():
    for stage, expected in [
        ("LAUNCH", "PDF_RENDER_FAILED_STAGE_LAUNCH"),
        ("SECRET=synthetic-private", "PDF_RENDER_FAILED_STAGE_UNVERIFIED"),
    ]:
        class Stub:
            def open(self, *_args, _stage=stage, **_kwargs):
                data = json.dumps({"error": "PDF_RENDER_FAILED", "stage": _stage,
                                   "token": "synthetic-private"}).encode()
                raise HTTPError("https://untrusted.invalid/", 500, "private",
                                {"Content-Type": "application/json"}, io.BytesIO(data))
        with patch.object(pdf, "build_opener", return_value=Stub()):
            with pytest.raises(pdf.PdfBlocked, match="^" + expected + "$"):
                pdf.http("https://wafl-pdf-dev.synthetic.workers.dev",
                         payload=b"{}", token="synthetic-secret")


def test_no_credential_or_r2_write_path():
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/wafl_pdf_dev_worker_only.py").read_text()
    workflow = (root / ".github/workflows/provider-gate-approval-core.yml").read_text()
    assert "wafl_pdf_dev_worker_only_v1" in workflow
    assert "wafl_pdf_dev_worker_only.py" in workflow
    assert "wafl_development_media_probe.py" in workflow
    assert "wafl_development_media_bootstrap.py" in workflow
    assert "wrangler\", \"secret" not in source
    assert "r2\", \"bucket" not in source
    assert "PDF_SOURCE_BLOB_DRIFT" in source
