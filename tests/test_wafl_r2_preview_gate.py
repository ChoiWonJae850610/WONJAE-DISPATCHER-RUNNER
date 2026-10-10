"""Synthetic only: no Cloudflare request or mutation in PR validation."""
from __future__ import annotations

import pytest

from scripts.wafl_r2_preview_gate import (
    GateError,
    deployment_version,
    require_bindings,
    require_dev_name,
    require_worker_origin,
    required_environment,
)


def test_nonproduction_names_cannot_be_confused_with_prod():
    assert require_dev_name("wafl-preview-worker", "WORKER") == "wafl-preview-worker"
    assert require_dev_name("wafl-r2-dev", "BUCKET") == "wafl-r2-dev"
    for unsafe in ("wafl-prod-worker", "wafl", "production-dev", "wafl/dev",
                   "wafl-dev.prod", "WAFL-dev", "wafl-debug"):
        with pytest.raises(GateError):
            require_dev_name(unsafe, "WORKER")


def test_worker_origin_exact_identity():
    require_worker_origin("https://wafl-preview-worker.owner.workers.dev", "wafl-preview-worker")
    for value in ("https://wafl-prod-worker.owner.workers.dev",
                  "http://wafl-preview-worker.owner.workers.dev",
                  "https://wafl-preview-worker.owner.workers.dev/secret",
                  "https://evil.example.com", "https://u:p@wafl-preview-worker.owner.workers.dev"):
        with pytest.raises(GateError):
            require_worker_origin(value, "wafl-preview-worker")


def test_binding_readback_requires_matching_existing_bucket_and_secret():
    data = {"resources": {"bindings": [
        {"name": "R2_BUCKET", "type": "r2_bucket", "bucket_name": "wafl-r2-dev"},
        {"name": "IMAGES", "type": "images"},
        {"name": "R2_WORKER_UPLOAD_SECRET", "type": "secret_text"},
    ]}}
    require_bindings(data, "wafl-r2-dev")
    for changed in (
        {"resources": {}},
        {"resources": {"bindings": data["resources"]["bindings"][:2]}},
        {"resources": {"bindings": [
            {"name": "R2_BUCKET", "type": "r2_bucket", "bucket_name": "wafl-r2-prod"},
            *data["resources"]["bindings"][1:],
        ]}},
    ):
        with pytest.raises(GateError):
            require_bindings(changed, "wafl-r2-dev")


def test_deployment_readback_no_ambiguous_or_unregistered_version():
    version_id = "a" * 40
    assert deployment_version([{"versions": [{"version_id": version_id}]}]) == version_id
    for item in ([], [{"versions": []}], [{"versions": [{"version_id": version_id},
                                                        {"version_id": "b" * 40}]}]):
        with pytest.raises(GateError):
            deployment_version(item)


def test_missing_credentials_and_entitlement_fail_before_provider_calls(monkeypatch):
    for key in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
                "WAFL_R2_PREVIEW_WORKER_NAME", "WAFL_R2_PREVIEW_BUCKET_NAME",
                "WAFL_R2_PREVIEW_WORKER_URL", "WAFL_R2_IMAGES_ENTITLEMENT_APPROVED",
                "WAFL_R2_SIGNING_PARITY_APPROVED"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(GateError, match="MISSING"):
        required_environment()
