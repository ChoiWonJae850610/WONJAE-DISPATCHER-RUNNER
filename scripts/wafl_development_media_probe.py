#!/usr/bin/env python3
"""Owner-only WAFL Development R2/PDF transport canary.

No Worker deployment, DB/Label writes, user credentials, Production, or mobile QA.
Only an unpredictable, newly absent synthetic R2 key may be PUT/GET/DELETE.
Secrets and signed URLs must never reach logs, artifacts, or GitHub summaries.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__:
    from .wafl_r2_preview_gate import (
        GateError, command_json, deployment_version, require_bindings,
        require_dev_name, require_worker_origin,
    )
else:
    from wafl_r2_preview_gate import (
        GateError, command_json, deployment_version, require_bindings,
        require_dev_name, require_worker_origin,
    )

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aYWcAAAAASUVORK5CYII="
)
MAX_PDF = 10 * 1024 * 1024
ENV_NAMES = (
    "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "WAFL_R2_PREVIEW_WORKER_NAME", "WAFL_R2_PREVIEW_BUCKET_NAME",
    "WAFL_R2_PREVIEW_WORKER_URL", "WAFL_R2_IMAGES_ENTITLEMENT_APPROVED",
    "WAFL_R2_SIGNING_PARITY_APPROVED", "WAFL_R2_WORKER_UPLOAD_SECRET",
    "WAFL_PDF_DEV_WORKER_NAME", "WAFL_PDF_DEV_WORKER_URL",
    "WAFLOW_PDF_GENERATOR_TOKEN",
)


class MediaBlocked(RuntimeError):
    """Public fixed error codes only. Never include external response details."""


def ensure(condition: bool, code: str) -> None:
    if not condition:
        raise MediaBlocked(code)


def environment() -> dict[str, str]:
    config = {key: os.environ.get(key, "").strip() for key in ENV_NAMES}
    ensure(all(config.values()), "MEDIA_EXISTING_SCOPED_SECRET_OR_IDENTITY_MISSING")
    ensure(
        re.fullmatch(r"[0-9a-fA-F]{32}", config["CLOUDFLARE_ACCOUNT_ID"]) is not None,
        "MEDIA_ACCOUNT_ID_INVALID",
    )
    ensure(config["WAFL_R2_IMAGES_ENTITLEMENT_APPROVED"] == "yes"
           and config["WAFL_R2_SIGNING_PARITY_APPROVED"] == "yes",
           "MEDIA_EXISTING_R2_OWNER_ATTESTATION_MISSING")
    r2 = config["WAFL_R2_PREVIEW_WORKER_NAME"]
    pdf = config["WAFL_PDF_DEV_WORKER_NAME"]
    try:
        require_dev_name(r2, "R2_WORKER")
        require_dev_name(pdf, "PDF_WORKER")
        require_dev_name(config["WAFL_R2_PREVIEW_BUCKET_NAME"], "R2_BUCKET")
        require_worker_origin(config["WAFL_R2_PREVIEW_WORKER_URL"], r2)
        require_worker_origin(config["WAFL_PDF_DEV_WORKER_URL"], pdf)
    except GateError:
        raise MediaBlocked("MEDIA_NONPRODUCTION_IDENTITY_MISMATCH") from None
    ensure(r2 != pdf, "MEDIA_WORKER_IDENTITIES_NOT_DISTINCT")
    return config


def source_identity(repo: Path, sha: str) -> None:
    ensure(re.fullmatch(r"[0-9a-f]{40}", sha) is not None,
           "MEDIA_SOURCE_SHA_INVALID")
    try:
        actual = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise MediaBlocked("MEDIA_SOURCE_IDENTITY_UNAVAILABLE") from None
    ensure(actual == sha, "MEDIA_SOURCE_SHA_MOVED")
    for path in (
        "cloudflare/r2-upload-worker.js",
        "cloudflare/pdf-generator-worker/src/index.js",
    ):
        entry = repo / path
        ensure(entry.is_file() and not entry.is_symlink(),
               "MEDIA_SOURCE_ENTRY_MISSING")


def existing_worker(name: str, env: dict[str, str]) -> dict:
    try:
        version = deployment_version(command_json(
            ["deployments", "list", "--name", name, "--json"], env,
        ))
        data = command_json(
            ["versions", "view", version, "--name", name, "--json"], env,
        )
    except GateError:
        raise MediaBlocked("MEDIA_EXISTING_WORKER_READBACK_UNVERIFIED") from None
    ensure(isinstance(data, dict), "MEDIA_WORKER_VERSION_INVALID")
    return data


def readback(config: dict[str, str]) -> None:
    env = dict(os.environ)
    env["CLOUDFLARE_SEND_METRICS"] = "false"
    env["CI"] = "true"
    try:
        require_bindings(
            existing_worker(config["WAFL_R2_PREVIEW_WORKER_NAME"], env),
            config["WAFL_R2_PREVIEW_BUCKET_NAME"],
        )
    except GateError:
        raise MediaBlocked("MEDIA_R2_BUCKET_BINDING_UNVERIFIED") from None
    pdf_view = existing_worker(config["WAFL_PDF_DEV_WORKER_NAME"], env)
    bindings = pdf_view.get("resources", {}).get("bindings")
    ensure(isinstance(bindings, list), "MEDIA_PDF_BINDINGS_UNAVAILABLE")
    browser = [b for b in bindings if isinstance(b, dict)
               and b.get("name") == "BROWSER" and b.get("type") == "browser"]
    token = [b for b in bindings if isinstance(b, dict)
             and b.get("name") in ("WAFLOW_PDF_GENERATOR_TOKEN", "PDF_GENERATOR_TOKEN")
             and b.get("type") == "secret_text"]
    ensure(len(browser) == 1 and len(token) == 1,
           "MEDIA_PDF_BROWSER_OR_SECRET_BINDING_MISSING")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request_bytes(url: str, *, method: str = "GET", data: bytes | None = None,
                  headers: dict[str, str] | None = None,
                  limit: int = MAX_PDF) -> tuple[int, dict[str, str], bytes]:
    ensure(urlsplit(url).scheme == "https", "MEDIA_HTTPS_REQUIRED")
    try:
        req = Request(url, data=data, headers=headers or {}, method=method)
        with build_opener(NoRedirect()).open(req, timeout=35) as response:
            body = response.read(limit + 1)
            ensure(len(body) <= limit, "MEDIA_RESPONSE_TOO_LARGE")
            return response.status, dict(response.headers.items()), body
    except HTTPError as error:
        return error.code, {}, b""
    except (URLError, OSError, TimeoutError, ValueError):
        raise MediaBlocked("MEDIA_NETWORK_UNVERIFIED") from None


def signed_url(origin: str, method: str, key: str, secret: str,
               *, size: int | None = None, content_type: str = "image/png") -> str:
    ensure(method in ("PUT", "GET", "DELETE"), "MEDIA_METHOD_UNSAFE")
    expiry = int(time.time()) + 60
    if method == "PUT":
        ensure(size is not None and 0 < size <= 1024, "MEDIA_CANARY_SIZE_UNSAFE")
        payload = "\n".join((method, key, content_type, str(expiry),
                              "size-v1", str(size)))
    else:
        payload = "\n".join((method, key, str(expiry)))
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    params = [("key", key), ("expires", str(expiry)), ("signature", signature)]
    if method == "PUT":
        params.extend((("contentType", content_type), ("fileSize", str(size))))
    return origin.rstrip("/") + "/?" + urlencode(params)


def canary_r2(config: dict[str, str]) -> None:
    key = (f"companies/{uuid.uuid4()}/workorders/{uuid.uuid4()}/"
           f"design/{uuid.uuid4()}.png")
    origin = config["WAFL_R2_PREVIEW_WORKER_URL"]
    secret = config["WAFL_R2_WORKER_UPLOAD_SECRET"]

    def call(method: str, body: bytes | None = None):
        url = signed_url(origin, method, key, secret,
                         size=len(body) if method == "PUT" and body else None)
        return request_bytes(
            url, method=method, data=body,
            headers={"Content-Type": "image/png"} if body else {},
            limit=4096,
        )

    status, _, _ = call("GET")
    ensure(status == 404, "MEDIA_R2_CANARY_NOT_VERIFIED_ABSENT")
    attempted_put = False
    error: MediaBlocked | None = None
    try:
        attempted_put = True
        status, _, result = call("PUT", PNG)
        ensure(status == 200 and json.loads(result).get("ok") is True,
               "MEDIA_R2_SIGNED_PUT_FAILED")
        status, headers, value = call("GET")
        content_type = next((v for k, v in headers.items()
                             if k.lower() == "content-type"), "")
        ensure(status == 200 and content_type.split(";")[0].lower() == "image/png"
               and hmac.compare_digest(
                   hashlib.sha256(value).digest(), hashlib.sha256(PNG).digest()),
               "MEDIA_R2_OBJECT_READBACK_MISMATCH")
    except (ValueError, TypeError):
        error = MediaBlocked("MEDIA_R2_RESPONSE_FORMAT_UNVERIFIED")
    except MediaBlocked as exc:
        error = exc
    finally:
        if attempted_put:
            # Only this run's unique, pre-proven-absent canary key is deleted.
            status, _, _ = call("DELETE")
            ensure(status == 200, "MEDIA_R2_CANARY_CLEANUP_UNVERIFIED")
            status, _, _ = call("GET")
            ensure(status == 404, "MEDIA_R2_CANARY_STILL_PRESENT")
    if error is not None:
        raise error


def pdf_bytes_ok(status: int, headers: dict[str, str], body: bytes) -> bool:
    content_type = next((v for k, v in headers.items()
                         if k.lower() == "content-type"), "")
    return (status == 200 and content_type.split(";")[0].strip().lower()
            == "application/pdf" and 20 <= len(body) <= MAX_PDF
            and body.startswith(b"%PDF-") and b"%%EOF" in body[-2048:])


def canary_pdf(config: dict[str, str]) -> None:
    origin = config["WAFL_PDF_DEV_WORKER_URL"].rstrip("/")
    status, _, health = request_bytes(origin + "/health", limit=2048)
    try:
        parsed = json.loads(health)
    except ValueError:
        raise MediaBlocked("MEDIA_PDF_HEALTH_INVALID") from None
    ensure(status == 200 and isinstance(parsed, dict)
           and parsed.get("ok") is True
           and parsed.get("service") == "waflow-pdf-generator",
           "MEDIA_PDF_EXISTING_SERVICE_IDENTITY_UNVERIFIED")
    payload = json.dumps({
        "html": "<!doctype html><html><body>WAFL Development media canary</body></html>",
        "fileName": "wafl-development-canary.pdf",
        "format": "A4", "orientation": "portrait",
    }).encode("utf-8")
    status, _, _ = request_bytes(
        origin + "/", method="POST", data=payload,
        headers={"Content-Type": "application/json"}, limit=2048,
    )
    ensure(status == 401, "MEDIA_PDF_UNAUTHENTICATED_REQUEST_NOT_DENIED")
    status, headers, pdf = request_bytes(
        origin + "/", method="POST", data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + config["WAFLOW_PDF_GENERATOR_TOKEN"],
            "Accept": "application/pdf",
        },
    )
    ensure(pdf_bytes_ok(status, headers, pdf), "MEDIA_PDF_AUTH_OR_BYTES_INVALID")


def execute(repo: Path, sha: str, summary: Path) -> None:
    source_identity(repo, sha)
    config = environment()  # fail before any remote request or mutation
    readback(config)         # existing non-Production versions and bindings first
    canary_r2(config)        # one exact synthetic object; cleanup is mandatory
    canary_pdf(config)       # ephemeral PDF byte stream; no storage
    with summary.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n## WAFL Development R2/PDF media transport\n"
            "- existing Development Worker/bucket and Browser Rendering: PASS\n"
            "- signed R2 synthetic upload, byte readback, exact cleanup: PASS\n"
            "- PDF auth denial, token-authorized render, PDF bytes: PASS\n"
            "- iPhone real Label/WorkOrder, Apple/Neon, Preview: NOT_RUN\n"
            "- Cloudflare deployment, DB, credentials, Production: NOT_RUN\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    try:
        execute(args.repo, args.source_sha, args.summary)
    except MediaBlocked as exc:
        print(f"::error::{exc}")
        return 1
    except Exception:
        print("::error::MEDIA_UNEXPECTED_UNVERIFIED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
