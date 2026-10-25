"""Entirely offline WAFL media canary / no remote Cloudflare effect in CI."""
from __future__ import annotations

import hashlib
import hmac
from email.message import Message
from urllib.error import HTTPError
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
    monkeypatch.setattr(media, "getproxies", lambda: {})
    with pytest.raises(media.MediaBlocked) as failure:
        media.canary_r2({
            "WAFL_R2_PREVIEW_WORKER_URL": "https://wafl-r2-dev.test.workers.dev",
            "WAFL_R2_WORKER_UPLOAD_SECRET": "synthetic-secret",
        })
    expected = f"MEDIA_R2_CANARY_NOT_VERIFIED_ABSENT_HTTP_{status}"
    if status == 403:
        expected += "_CT_UNKNOWN_CF_CHL_UNKNOWN_CF_ERR_UNKNOWN_HTTPS_PXY_NONE"
    assert str(failure.value) == expected
    assert methods == ["GET"]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("status", [None, "401", True, 0, 99, 600])
def test_r2_precheck_unknown_status_never_reflects_untrusted_text(status):
    assert media.r2_absence_failure_code(status) == (
        "MEDIA_R2_CANARY_NOT_VERIFIED_ABSENT_HTTP_UNVERIFIED"
    )


@pytest.mark.parametrize("raw,kind", [
    ("text/html; charset=UTF-8", "HTML"),
    ("application/json", "JSON"),
    ("application/problem+json", "JSON"),
    ("text/plain", "TEXT"),
    ("application/octet-stream", "OTHER"),
    (None, "NONE"),
])
def test_403_header_classification_never_reflects_raw_values(raw, kind):
    headers = {"Private-Header": "sensitive-private-value"}
    if raw is not None:
        headers["CONTENT-Type"] = raw
    assert media.safe_403_response_metadata(headers) == {
        "_ct": kind, "_cf_chl": "NO", "_cf_err": "NO",
    }


def test_cloudflare_header_presence_not_waf_block_proof():
    flags = media.safe_403_response_metadata({
        "CF-Mitigated": "challenge",
        "CF-Error-Type": "private-error-code",
        "Content-Type": "text/html; charset=private-charset",
        "CF-Ray": "private-ray", "Set-Cookie": "private-cookie",
    })
    assert flags == {"_ct": "HTML", "_cf_chl": "YES", "_cf_err": "YES"}
    assert "private" not in str(flags)
    assert media.safe_403_response_metadata({
        "CF-Ray": "private-ray",
    }) == {"_ct": "NONE", "_cf_chl": "NO", "_cf_err": "NO"}
    assert media.safe_403_response_metadata({
        "CF-Chl-Test": "private",
    })["_cf_chl"] == "YES"


def test_real_http_error_403_discards_raw_headers_and_body(monkeypatch, capsys):
    incoming = Message()
    incoming["Content-Type"] = "text/html; charset=private"
    incoming["CF-Mitigated"] = "challenge"
    incoming["CF-Error-Origin"] = "private-origin"
    incoming["Set-Cookie"] = "private-cookie"

    class DeniedOpener:
        def open(self, request, timeout):
            headers = {k.lower(): v for k, v in request.header_items()}
            assert headers["user-agent"] == media.MEDIA_PROBE_USER_AGENT
            raise HTTPError(request.full_url, 403, "private-message", incoming, None)

    monkeypatch.setattr(media, "build_opener", lambda *_: DeniedOpener())
    status, flags, body = media.request_bytes(
        "https://wafl-r2-dev.test.workers.dev/?key=private&signature=private",
    )
    assert status == 403 and body == b""
    assert flags == {"_ct": "HTML", "_cf_chl": "YES", "_cf_err": "YES"}
    assert "private" not in str(flags)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("configured,bypass,expected", [
    (None, False, "NONE"),
    ("http://private-proxy.example:8080", False, "CONFIGURED"),
    ("http://private-proxy.example:8080", True, "BYPASS"),
])
def test_https_proxy_detection_reports_only_enum(
    monkeypatch, configured, bypass, expected,
):
    monkeypatch.setattr(media, "getproxies",
                        lambda: {"https": configured} if configured else {})
    monkeypatch.setattr(media, "proxy_bypass", lambda _host: bypass)
    outcome = media.https_proxy_hint("https://wafl-r2-dev.test.workers.dev/")
    assert outcome == expected
    assert "private" not in outcome


def test_https_proxy_detection_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(media, "getproxies", lambda: {"https": "private"})
    def cannot_check(_host):
        raise OSError("private-url")
    monkeypatch.setattr(media, "proxy_bypass", cannot_check)
    assert media.https_proxy_hint(
        "https://wafl-r2-dev.test.workers.dev/"
    ) == "UNKNOWN"


def test_403_canary_logs_only_whitelisted_flags_never_uploads(
    monkeypatch, capsys,
):
    monkeypatch.setattr(media, "getproxies", lambda: {})
    methods = []

    def fake_request(_url, *, method="GET", data=None, headers=None, limit=None):
        methods.append(method)
        return 403, {
            "_ct": "sensitive-category", "_cf_chl": "YES",
            "_cf_err": "NO", "Authorization": "private-token",
        }, b"private-body"

    monkeypatch.setattr(media, "request_bytes", fake_request)
    with pytest.raises(media.MediaBlocked) as failure:
        media.canary_r2({
            "WAFL_R2_PREVIEW_WORKER_URL": "https://wafl-r2-dev.test.workers.dev",
            "WAFL_R2_WORKER_UPLOAD_SECRET": "private-secret",
        })
    assert str(failure.value) == (
        "MEDIA_R2_CANARY_NOT_VERIFIED_ABSENT_HTTP_403"
        "_CT_UNKNOWN_CF_CHL_YES_CF_ERR_NO_HTTPS_PXY_NONE"
    )
    assert methods == ["GET"]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_safe_403_malformed_headers_are_bounded():
    class BrokenHeaders:
        def items(self):
            raise ValueError("private-response")
    assert media.safe_403_response_metadata(BrokenHeaders()) == {
        "_ct": "UNKNOWN", "_cf_chl": "UNKNOWN", "_cf_err": "UNKNOWN",
    }


@pytest.mark.parametrize("method,payload,request_headers", [
    ("GET", None, {}),
    ("PUT", media.WEBP, {"Content-Type": "image/webp"}),
    ("DELETE", None, {}),
    ("POST", b"{}", {
        "Content-Type": "application/json",
        "Authorization": "Bearer synthetic-private-token",
    }),
])
def test_worker_requests_use_fixed_service_user_agent_and_preserve_auth(
    monkeypatch, capsys, method, payload, request_headers,
):
    observed = []

    class FakeResponse:
        status = 200
        headers = Message()

        def __enter__(self):
            return self

        def __exit__(self, _kind, _value, _traceback):
            return False

        def read(self, _limit):
            return b"ok"

    class FakeOpener:
        def open(self, request, timeout):
            observed.append({
                "method": request.get_method(),
                "headers": {k.lower(): v for k, v in request.header_items()},
                "body": request.data,
                "url": request.full_url,
                "timeout": timeout,
            })
            return FakeResponse()

    monkeypatch.setattr(media, "build_opener", lambda *_: FakeOpener())
    url = ("https://wafl-r2-dev.example.workers.dev/"
           "?key=synthetic&signature=synthetic-private-signature")
    status, headers, body = media.request_bytes(
        url, method=method, data=payload, headers=request_headers,
    )

    assert media.MEDIA_PROBE_USER_AGENT == "WAFL-Development-Media-Probe/1.0"
    assert status == 200 and body == b"ok" and headers == {}
    assert len(observed) == 1
    actual = observed[0]
    assert actual["url"] == url
    assert actual["method"] == method
    assert actual["body"] == payload
    assert actual["timeout"] == 35
    assert actual["headers"]["user-agent"] == media.MEDIA_PROBE_USER_AGENT
    for key, value in request_headers.items():
        assert actual["headers"][key.lower()] == value

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
