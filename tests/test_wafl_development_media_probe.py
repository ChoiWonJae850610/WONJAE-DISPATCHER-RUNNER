"""Entirely offline WAFL media canary / no remote Cloudflare effect in CI."""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import wafl_development_media_probe as media


def test_dev_only_names_and_secrets_before_network(monkeypatch):
    for key in media.ENV_NAMES:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(media.MediaBlocked, match="MISSING"):
        media.environment()
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "f" * 32)
    for key in media.ENV_NAMES:
        if key != "CLOUDFLARE_ACCOUNT_ID":
            monkeypatch.setenv(key, "yes")
    monkeypatch.setenv("WAFL_R2_IMAGES_ENTITLEMENT_APPROVED", "no")
    monkeypatch.setenv("WAFL_R2_PREVIEW_WORKER_NAME", "wafl-preview-worker")
    monkeypatch.setenv("WAFL_R2_PREVIEW_BUCKET_NAME", "wafl-r2-dev")
    monkeypatch.setenv("WAFL_R2_PREVIEW_WORKER_URL",
                       "https://wafl-preview-worker.example.workers.dev")
    monkeypatch.setenv("WAFL_PDF_DEV_WORKER_NAME", "waflow-pdf-dev")
    monkeypatch.setenv("WAFL_PDF_DEV_WORKER_URL",
                       "https://waflow-pdf-dev.example.workers.dev")
    assert media.environment()["WAFL_PDF_DEV_WORKER_NAME"] == "waflow-pdf-dev"
    monkeypatch.setenv("WAFL_PDF_DEV_WORKER_URL",
                       "https://waflow-pdf-prod.example.workers.dev")
    with pytest.raises(media.MediaBlocked, match="NONPRODUCTION"):
        media.environment()


def test_signed_canary_put_payload_size_bound():
    origin = "https://wafl-r2-dev.owner.workers.dev"
    key = "companies/a/workorders/b/design/c.webp"
    url = media.signed_url(origin, "PUT", key, "private", size=31, content_type="image/webp")
    query = parse_qs(urlsplit(url).query)
    payload = "\n".join(("PUT", key, "image/webp", query["expires"][0],
                         "size-v1", "31"))
    expected = hmac.new(b"private", payload.encode(), hashlib.sha256).hexdigest()
    assert query["signature"] == [expected]
    assert query["fileSize"] == ["31"]
    assert media.WEBP.startswith(b"RIFF") and media.WEBP[8:12] == b"WEBP"
    assert len(media.WEBP) >= 20
    for method in ("PATCH", "POST"):
        with pytest.raises(media.MediaBlocked, match="METHOD"):
            media.signed_url(origin, method, key, "private")
    with pytest.raises(media.MediaBlocked, match="SIZE"):
        media.signed_url(origin, "PUT", key, "private", size=100000)


def test_pdf_validation_never_accepts_html_or_partial_pdf():
    assert media.pdf_bytes_ok(
        200, {"Content-Type": "application/pdf"},
        b"%PDF-1.7\n" + b"hello" * 30 + b"\n%%EOF",
    )
    for status, content_type, body in (
        (200, "text/html", b"%PDF-" + b"x" * 40 + b"%%EOF"),
        (401, "application/pdf", b"%PDF-" + b"x" * 40 + b"%%EOF"),
        (200, "application/pdf", b"not a pdf"),
        (200, "application/pdf", b"%PDF-" + b"x" * 40),
    ):
        assert not media.pdf_bytes_ok(
            status, {"Content-Type": content_type}, body,
        )


def test_r2_synthetic_only_absent_upload_read_and_exact_cleanup(monkeypatch):
    observed = {}
    methods = []
    def fake_request(url, *, method="GET", data=None, headers=None, limit=None):
        query = parse_qs(urlsplit(url).query)
        key = query["key"][0]
        assert "/design/" in key and key.endswith(".webp")
        assert query["signature"][0]
        methods.append(method)
        if method == "PUT":
            assert "fileSize" in query and data == media.WEBP
            assert key not in observed
            observed[key] = data
            return 200, {}, b'{"ok":true}'
        if method == "DELETE":
            del observed[key]
            return 200, {}, b'{"ok":true}'
        if key in observed:
            return 200, {"Content-Type": "image/webp"}, observed[key]
        return 404, {}, b""
    monkeypatch.setattr(media, "request_bytes", fake_request)
    media.canary_r2({
        "WAFL_R2_PREVIEW_WORKER_URL": "https://wafl-r2-dev.owner.workers.dev",
        "WAFL_R2_WORKER_UPLOAD_SECRET": "private",
    })
    assert observed == {}
    assert methods == ["GET", "PUT", "GET", "DELETE", "GET"]


def test_missing_browser_binding_blocks_before_live_canary(monkeypatch):
    data = {"resources": {"bindings": [
        {"name": "R2_BUCKET", "type": "r2_bucket", "bucket_name": "wafl-r2-dev"},
        {"name": "R2_WORKER_UPLOAD_SECRET", "type": "secret_text"},
    ]}}
    def fake_worker(name, env):
        if name == "wafl-r2-dev":
            return data
        return {"resources": {"bindings": [
            {"name": "WAFLOW_PDF_GENERATOR_TOKEN", "type": "secret_text"},
        ]}}
    monkeypatch.setattr(media, "existing_worker", fake_worker)
    with pytest.raises(media.MediaBlocked, match="PDF_BROWSER"):
        media.readback({
            "WAFL_R2_PREVIEW_WORKER_NAME": "wafl-r2-dev",
            "WAFL_R2_PREVIEW_BUCKET_NAME": "wafl-r2-dev",
            "WAFL_PDF_DEV_WORKER_NAME": "waflow-pdf-dev",
        })


@pytest.mark.parametrize("status", [200, 301, 401, 403, 500, 503])
def test_r2_precheck_reports_only_http_status_without_mutation(
    monkeypatch, capsys, status,
):
    methods = []

    def fake_request(_url, *, method="GET", data=None, headers=None, limit=None):
        methods.append(method)
        return status, {"X-Private-Test": "private-header"}, b"private-body"

    monkeypatch.setattr(media, "request_bytes", fake_request)
    with pytest.raises(media.MediaBlocked) as failure:
        media.canary_r2({
            "WAFL_R2_PREVIEW_WORKER_URL": "https://wafl-r2-dev.test.workers.dev",
            "WAFL_R2_WORKER_UPLOAD_SECRET": "synthetic-secret",
        })
    assert str(failure.value) == (
        f"MEDIA_R2_CANARY_NOT_VERIFIED_ABSENT_HTTP_{status}"
    )
    assert methods == ["GET"]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("status", [None, "401", True, 0, 99, 600])
def test_r2_precheck_unknown_status_never_reflects_untrusted_text(status):
    assert media.r2_absence_failure_code(status) == (
        "MEDIA_R2_CANARY_NOT_VERIFIED_ABSENT_HTTP_UNVERIFIED"
    )
