#!/usr/bin/env python3
"""Single authorized Owner gate: deploy existing WAFL Dev PDF Worker then prove PDF bytes.

No R2 call, secret creation, billing mutation, customer data, or Production action.
All errors emitted are fixed classifications, never remote text.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from wafl_development_media_probe import NoRedirect, pdf_bytes_ok, pdf_content_type_category
from wafl_r2_preview_gate import (
    GateError,
    command_json,
    deployment_version,
    require_dev_name,
    require_worker_origin,
)

RUNNER = "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER"
TARGET = "wafl-pdf-dev"
ENTRY = "cloudflare/pdf-generator-worker/src/index.js"
LOCK = "cloudflare/pdf-generator-worker/package-lock.json"
STAGES = frozenset((
    "BROWSER_BINDING", "LAUNCH", "NEW_PAGE", "JS_DISABLE",
    "INTERCEPTION_SETUP", "SET_CONTENT", "PAGE_PDF", "OUTPUT", "CLOSE",
))
MAX_PDF = 10 * 1024 * 1024


class PdfBlocked(RuntimeError):
    """Never contain raw third-party exception messages."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise PdfBlocked(code)


def scoped_environment() -> dict[str, str]:
    names = (
        "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
        "WAFL_PDF_DEV_WORKER_NAME", "WAFL_PDF_DEV_WORKER_URL",
        "WAFLOW_PDF_GENERATOR_TOKEN",
    )
    config = {key: os.getenv(key, "").strip() for key in names}
    require(all(config.values()), "PDF_EXISTING_CREDENTIAL_OR_IDENTITY_UNAVAILABLE")
    require(os.getenv("GITHUB_REPOSITORY") == RUNNER
            and os.getenv("GITHUB_REF") == "refs/heads/main"
            and os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch",
            "PDF_TRUSTED_OWNER_WORKFLOW_REQUIRED")
    require(re.fullmatch(r"[0-9a-fA-F]{32}", config["CLOUDFLARE_ACCOUNT_ID"]) is not None,
            "PDF_ACCOUNT_ID_INVALID")
    name = config["WAFL_PDF_DEV_WORKER_NAME"]
    try:
        require_dev_name(name, "PDF_WORKER")
        require_worker_origin(config["WAFL_PDF_DEV_WORKER_URL"], name)
    except GateError:
        raise PdfBlocked("PDF_WORKER_IDENTITY_INVALID") from None
    require(name == TARGET, "PDF_WORKER_SCOPE_NOT_EXACT")
    return config


def source_identity(repo: Path, sha: str, baseline: str, blob: str, lock_blob: str) -> Path:
    for item in (sha, baseline, blob, lock_blob):
        require(re.fullmatch(r"[0-9a-f]{40}", item) is not None, "PDF_SOURCE_PIN_INVALID")
    require(repo.is_dir() and not repo.is_symlink(), "PDF_SOURCE_DIRECTORY_INVALID")

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(repo), *args], text=True, capture_output=True,
                check=False, timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise PdfBlocked("PDF_SOURCE_READ_UNAVAILABLE") from None

    head = git("rev-parse", "HEAD")
    require(head.returncode == 0 and head.stdout.strip() == sha,
            "PDF_SOURCE_SHA_MISMATCH")
    require(git("merge-base", "--is-ancestor", baseline, sha).returncode == 0,
            "PDF_BASELINE_NOT_ANCESTOR")
    for path, expected in ((ENTRY, blob), (LOCK, lock_blob)):
        f = repo / path
        require(f.is_file() and not f.is_symlink(), "PDF_SOURCE_ENTRY_MISSING")
        result = git("hash-object", "--", path)
        require(result.returncode == 0 and result.stdout.strip() == expected,
                "PDF_SOURCE_BLOB_DRIFT")
    package = repo / "cloudflare/pdf-generator-worker/package.json"
    require(package.is_file() and not package.is_symlink(), "PDF_PACKAGE_MISSING")
    return package.parent


def run_quiet(args: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> None:
    try:
        result = subprocess.run(
            args, cwd=cwd, env=env, text=True, capture_output=True,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PdfBlocked("PDF_DEPLOY_COMMAND_UNVERIFIED") from None
    require(result.returncode == 0, "PDF_DEPLOY_COMMAND_FAILED")


def deployed_version(env: dict[str, str]) -> str:
    try:
        data = command_json(["deployments", "list", "--name", TARGET, "--json"], env)
        version = deployment_version(data)
        view = command_json(
            ["versions", "view", version, "--name", TARGET, "--json"], env,
        )
    except GateError:
        raise PdfBlocked("PDF_EXISTING_DEPLOYMENT_READBACK_UNVERIFIED") from None
    require(isinstance(view, dict), "PDF_DEPLOYMENT_METADATA_INVALID")
    resources = view.get("resources")
    bindings = resources.get("bindings") if isinstance(resources, dict) else None
    require(isinstance(bindings, list) and all(isinstance(b, dict) for b in bindings),
            "PDF_BINDINGS_UNVERIFIED")
    expected = {
        "BROWSER": "browser",
        "WAFLOW_PDF_GENERATOR_TOKEN": "secret_text",
        "WAFLOW_PDF_GENERATOR_TIMEOUT_MS": "plain_text",
    }
    names = [b.get("name") for b in bindings]
    require(len(names) == len(set(names)) and
            all(isinstance(n, str) and n in expected for n in names),
            "PDF_EXTRA_OR_DUPLICATE_BINDING")
    for key, kind in expected.items():
        match = [b for b in bindings if b.get("name") == key]
        if key == "WAFLOW_PDF_GENERATOR_TIMEOUT_MS" and not match:
            continue
        require(len(match) == 1 and match[0].get("type") == kind,
                "PDF_BROWSER_OR_SECRET_BINDING_MISMATCH")
    return version


def http(origin: str, *, payload: bytes | None = None,
         token: str | None = None) -> tuple[int, dict[str, str], bytes]:
    path = "/health" if payload is None else "/"
    headers = {"User-Agent": "WAFL-PDF-Dev-Owner-Canary/1.0"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = "Bearer " + token
        headers["Accept"] = "application/pdf"
    request = Request(origin.rstrip("/") + path, data=payload, headers=headers,
                      method="GET" if payload is None else "POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=40) as response:
            content = response.read(MAX_PDF + 1 if token else 2049)
            require(len(content) <= (MAX_PDF if token else 2048),
                    "PDF_CANARY_RESPONSE_OVERSIZED")
            return response.status, dict(response.headers.items()), content
    except HTTPError as error:
        kind = pdf_content_type_category(error.headers)
        stage = "UNVERIFIED"
        if kind == "JSON" and error.code == 500 and token:
            try:
                body = error.read(1025)
                if len(body) <= 1024:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict) and parsed.get("error") == "PDF_RENDER_FAILED":
                        value = parsed.get("stage")
                        if type(value) is str and value in STAGES:
                            stage = value
            except (ValueError, TypeError, UnicodeError, OSError):
                pass
            raise PdfBlocked("PDF_RENDER_FAILED_STAGE_" + stage) from None
        if error.code == 401 and token is None and payload is not None:
            return 401, {}, b""
        raise PdfBlocked("PDF_CANARY_UNEXPECTED_HTTP_RESPONSE") from None
    except (URLError, OSError, TimeoutError, ValueError):
        raise PdfBlocked("PDF_CANARY_NETWORK_UNVERIFIED") from None


def canary(config: dict[str, str]) -> None:
    origin = config["WAFL_PDF_DEV_WORKER_URL"]
    status, _, health = http(origin)
    try:
        metadata = json.loads(health)
    except (ValueError, UnicodeError):
        raise PdfBlocked("PDF_SERVICE_HEALTH_UNVERIFIED") from None
    require(status == 200 and isinstance(metadata, dict)
            and metadata.get("ok") is True
            and metadata.get("service") == "waflow-pdf-generator",
            "PDF_SERVICE_IDENTITY_MISMATCH")
    sample = json.dumps({
        "html": "<!doctype html><html><body>WAFL Dev PDF</body></html>",
        "fileName": "wafl-dev-probe.pdf", "format": "A4", "orientation": "portrait",
    }).encode("utf-8")
    denied, _, _ = http(origin, payload=sample)
    require(denied == 401, "PDF_UNAUTHENTICATED_401_NOT_PROVEN")
    status, headers, body = http(
        origin, payload=sample, token=config["WAFLOW_PDF_GENERATOR_TOKEN"],
    )
    require(pdf_bytes_ok(status, headers, body),
            "PDF_AUTHENTICATED_BYTES_UNVERIFIED")


def execute(repo: Path, sha: str, baseline: str, blob: str, lock_blob: str,
            summary: Path) -> None:
    pdf_dir = source_identity(repo, sha, baseline, blob, lock_blob)
    config = scoped_environment()
    env = dict(os.environ)
    env["CLOUDFLARE_SEND_METRICS"] = "false"
    env["CI"] = "true"
    before = deployed_version(env)
    with tempfile.TemporaryDirectory(prefix="wafl-pdf-owner-") as temp:
        path = Path(temp) / "pdf.toml"
        path.write_text(
            'name = "wafl-pdf-dev"\n'
            + "main = " + json.dumps(str((repo / ENTRY).resolve())) + "\n"
            + 'workers_dev = true\nkeep_vars = true\n'
            + 'compatibility_date = "2026-05-01"\n'
            + 'compatibility_flags = ["nodejs_compat"]\n'
            + '[browser]\nbinding = "BROWSER"\n'
            + '[vars]\nWAFLOW_PDF_GENERATOR_TIMEOUT_MS = "30000"\n',
            encoding="utf-8",
        )
        run_quiet(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                  cwd=pdf_dir, env=env, timeout=300)
        argv = ["wrangler", "deploy", "--config", str(path), "--keep-vars"]
        run_quiet([*argv, "--dry-run"], cwd=pdf_dir, env=env, timeout=180)
        require(deployed_version(env) == before, "PDF_CONCURRENT_VERSION_CHANGE")
        # The only provider mutation is deployment of one existing exact Worker.
        run_quiet(argv, cwd=pdf_dir, env=env, timeout=240)
        after = deployed_version(env)
        require(after != before, "PDF_DEPLOYMENT_VERSION_NOT_ADVANCED")
    # Failed live Browser Run is not PASS, even if deployment was successful.
    canary(config)
    with summary.open("a", encoding="utf-8") as output:
        output.write(
            "\n## WAFL PDF Development Worker-only acceptance\n"
            "- existing wafl-pdf-dev Worker version advanced: PASS\n"
            "- Browser and bearer bindings retained: PASS\n"
            "- unauthenticated 401 and authenticated PDF bytes: PASS\n"
            "- Git PDF Worker source and npm lock pinned: PASS\n"
            "- Cloudflare Browser Run account quota: UNVERIFIED\n"
            "- R2, credentials, paid plan, Vercel, EAS, Production: NOT_RUN\n"
            "- Preview/real iPhone WorkOrder acceptance: NOT_RUN\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--source-blob", required=True)
    parser.add_argument("--lock-blob", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    try:
        execute(args.repo, args.source_sha, args.baseline_sha,
                args.source_blob, args.lock_blob, args.summary)
    except PdfBlocked as error:
        print("::error::" + str(error))
        return 1
    except Exception:
        print("::error::PDF_OWNER_WORKER_UNEXPECTED_UNVERIFIED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
