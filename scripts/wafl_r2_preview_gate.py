#!/usr/bin/env python3
"""Exact-SHA WAFL Development R2 *existing* Worker delivery (Owner-only trusted workflow).

No product credentials are committed, persisted or printed. Reads existing Worker
and bucket identity, refuses missing bindings, and never creates secrets or buckets.
This is NOT an API/image-upload/physical QA runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class GateError(RuntimeError):
    """Fail-closed public classification; never contain provider stderr or secrets."""


def require_dev_name(value: str, label: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,62}", value):
        raise GateError(f"{label}_UNSAFE")
    if not re.search(r"(?:^|-)(?:dev|preview|test)(?:-|$)", value):
        raise GateError(f"{label}_NOT_NONPRODUCTION")
    if "prod" in value:
        raise GateError(f"{label}_PRODUCTION_FORBIDDEN")
    return value


def require_worker_origin(raw_url: str, worker: str) -> None:
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme != "https" or parsed.username or parsed.password
        or parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        or not parsed.hostname or not parsed.hostname.endswith(".workers.dev")
        or parsed.hostname.split(".")[0] != worker
    ):
        raise GateError("WORKER_URL_IDENTITY_MISMATCH")


def _bindings(document: dict) -> list[dict]:
    resources = document.get("resources")
    if not isinstance(resources, dict):
        raise GateError("BINDING_READBACK_UNAVAILABLE")
    result = resources.get("bindings")
    if not isinstance(result, list) or not all(isinstance(x, dict) for x in result):
        raise GateError("BINDING_READBACK_UNAVAILABLE")
    return result


def require_bindings(document: dict, bucket: str) -> None:
    bindings = _bindings(document)
    expected = {
        "R2_BUCKET": "r2_bucket",
        "IMAGES": "images",
        "R2_WORKER_UPLOAD_SECRET": "secret_text",
    }
    for name, kind in expected.items():
        matches = [b for b in bindings if b.get("name") == name]
        if len(matches) != 1:
            raise GateError(f"{name}_BINDING_MISSING_OR_DUPLICATE")
        binding = matches[0]
        if binding.get("type") != kind:
            raise GateError(f"{name}_BINDING_TYPE_MISMATCH")
        if name == "R2_BUCKET":
            remote_bucket = binding.get("bucket_name") or binding.get("bucketName")
            if remote_bucket is None or remote_bucket != bucket:
                raise GateError("R2_BUCKET_IDENTITY_NOT_VERIFIED")


def deployment_version(data: object) -> str:
    if isinstance(data, dict):
        data = data.get("deployments")
    if not isinstance(data, list) or not data:
        raise GateError("EXISTING_WORKER_DEPLOYMENT_NOT_VERIFIED")
    last = data[-1]
    if not isinstance(last, dict):
        raise GateError("DEPLOYMENT_FORMAT_UNVERIFIED")
    versions = last.get("versions")
    if not isinstance(versions, list) or len(versions) != 1 or not isinstance(versions[0], dict):
        raise GateError("DEPLOYMENT_VERSIONS_AMBIGUOUS")
    version = versions[0].get("version_id")
    if not isinstance(version, str) or not re.fullmatch(r"[a-fA-F0-9-]{30,50}", version):
        raise GateError("DEPLOYMENT_VERSION_UNVERIFIED")
    return version


def command_json(args: list[str], env: dict[str, str]) -> object:
    result = subprocess.run(
        ["wrangler", *args], capture_output=True, text=True, check=False,
        env=env, timeout=90,
    )
    if result.returncode:
        raise GateError("CLOUDFLARE_READBACK_FAILED")
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise GateError("CLOUDFLARE_JSON_UNVERIFIED") from exc


def command_quiet(args: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(
        ["wrangler", *args], capture_output=True, text=True, check=False,
        env=env, timeout=240,
    )
    if result.returncode:
        raise GateError("CLOUDFLARE_WRANGLER_STEP_FAILED")


def required_environment() -> dict[str, str]:
    names = (
        "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
        "WAFL_R2_PREVIEW_WORKER_NAME", "WAFL_R2_PREVIEW_BUCKET_NAME",
        "WAFL_R2_PREVIEW_WORKER_URL", "WAFL_R2_IMAGES_ENTITLEMENT_APPROVED",
        "WAFL_R2_SIGNING_PARITY_APPROVED",
    )
    values = {key: os.environ.get(key, "").strip() for key in names}
    if not all(values.values()):
        raise GateError("WAFL_SCOPED_CREDENTIAL_OR_OWNER_READBACK_MISSING")
    if values["WAFL_R2_IMAGES_ENTITLEMENT_APPROVED"] != "yes":
        raise GateError("IMAGES_ENTITLEMENT_NOT_APPROVED")
    if values["WAFL_R2_SIGNING_PARITY_APPROVED"] != "yes":
        raise GateError("WORKER_SIGNING_PARITY_NOT_APPROVED")
    account = values["CLOUDFLARE_ACCOUNT_ID"]
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account):
        raise GateError("CLOUDFLARE_ACCOUNT_UNVERIFIED")
    return values


def execute(repo: Path, sha: str, summary: Path) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise GateError("SOURCE_SHA_INVALID")
    actual = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    if actual.returncode or actual.stdout.strip() != sha:
        raise GateError("PRODUCT_SOURCE_SHA_MISMATCH")
    worker_entry = repo / "cloudflare" / "r2-upload-worker.js"
    if worker_entry.is_symlink() or not worker_entry.is_file():
        raise GateError("WORKER_SOURCE_MISSING")
    config = required_environment()
    worker = require_dev_name(config["WAFL_R2_PREVIEW_WORKER_NAME"], "WORKER")
    bucket = require_dev_name(config["WAFL_R2_PREVIEW_BUCKET_NAME"], "BUCKET")
    require_worker_origin(config["WAFL_R2_PREVIEW_WORKER_URL"], worker)
    scope_env = dict(os.environ)
    scope_env["CLOUDFLARE_SEND_METRICS"] = "false"
    scope_env["CI"] = "true"

    def deployment_readback() -> tuple[str, dict]:
        payload = command_json(
            ["deployments", "list", "--name", worker, "--json"], scope_env,
        )
        version_id = deployment_version(payload)
        view = command_json(
            ["versions", "view", version_id, "--name", worker, "--json"],
            scope_env,
        )
        if not isinstance(view, dict):
            raise GateError("VERSION_READBACK_FORMAT_UNVERIFIED")
        require_bindings(view, bucket)
        return version_id, view

    before, _ = deployment_readback()
    with tempfile.TemporaryDirectory(prefix="wafl-r2-dev-") as temp:
        wrangler_config = Path(temp) / "wrangler.toml"
        source = worker_entry.resolve()
        wrangler_config.write_text(
            f'name = "{worker}"\n'
            f'main = "{source.as_posix()}"\n'
            'compatibility_date = "2026-08-20"\n'
            'keep_vars = true\n'
            '[images]\nbinding = "IMAGES"\n'
            '[[r2_buckets]]\n'
            'binding = "R2_BUCKET"\n'
            f'bucket_name = "{bucket}"\n',
            encoding="utf-8",
        )
        args = ["deploy", "--config", str(wrangler_config), "--keep-vars"]
        command_quiet([*args, "--dry-run"], scope_env)
        # Re-check exact incumbent version after dry-run, before mutation.
        check, _ = deployment_readback()
        if check != before:
            raise GateError("WORKER_CONCURRENT_CHANGE")
        command_quiet(args, scope_env)
        after, _ = deployment_readback()
        if after == before:
            raise GateError("NEW_WORKER_VERSION_NOT_VERIFIED")
    proof = hashlib.sha256((sha + ":" + after).encode()).hexdigest()[:12]
    with summary.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n## WAFL Development R2 existing Worker\n"
            f"- source SHA: \`{sha}\`\n"
            f"- evidence ref (non-secret digest): \`{proof}\`\n"
            "- existing non-Production Worker redeploy and binding readback: PASS\n"
            "- signed API / R2 object mutation QA: NOT_RUN\n"
            "- Vercel Preview API / iOS OTA / device / PDF QA: NOT_RUN\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()
    try:
        execute(Path(args.repo), args.source_sha, Path(args.summary))
    except (GateError, OSError, subprocess.TimeoutExpired) as exc:
        classification = str(exc) if isinstance(exc, GateError) else "R2_GATE_ENVIRONMENT_ERROR"
        print(f"::error::WAFL R2 provider gate blocked: {classification}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
