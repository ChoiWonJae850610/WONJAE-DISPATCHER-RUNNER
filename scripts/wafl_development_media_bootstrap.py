#!/usr/bin/env python3
"""Exact-name, new-resource-only WAFL Development Cloudflare bootstrap.

Only the separately approved SANJINWORKS Owner provider gate may run this.
No PR validation executes provider or credential operations.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ACCOUNT_HOST = "https://api.cloudflare.com/client/v4"
RUNNER_REPOSITORY = "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER"
PRODUCT = "ChoiWonJae850610/WAFL"
BUCKET = "wafl-media-dev"
IMAGE_WORKER = "wafl-r2-dev"
PDF_WORKER = "wafl-pdf-dev"
LEGACY_NAMES = frozenset(
    ("peacebypiece-r2-upload", "peacebypiece-files", "waflow-pdf-generator")
)
# Names that this isolated bootstrap may CREATE (and must first prove absent).
# Uses existing authenticated Owner/Cloudflare credentials only. No billing plan
# upgrades, Cloudflare Images transformations, or entitlement Secret writes.
SECRET_NAMES = (
    "WAFL_R2_PREVIEW_WORKER_NAME", "WAFL_R2_PREVIEW_BUCKET_NAME",
    "WAFL_R2_PREVIEW_WORKER_URL", "WAFL_R2_WORKER_UPLOAD_SECRET",
    "WAFL_PDF_DEV_WORKER_NAME", "WAFL_PDF_DEV_WORKER_URL",
    "WAFL_PDF_GENERATOR_TOKEN", "WAFL_R2_SIGNING_PARITY_APPROVED",
)


class BootstrapBlocked(RuntimeError):
    """Safe public failure code, never include secrets, URLs or provider stderr."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise BootstrapBlocked(code)


def command(args: list[str], *, env: dict[str, str], cwd: Path | None = None,
            stdin: str | None = None, timeout: int = 180) -> None:
    try:
        result = subprocess.run(
            args, input=stdin, text=True, capture_output=True, check=False,
            env=env, cwd=cwd, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BootstrapBlocked("BOOTSTRAP_COMMAND_UNVERIFIED") from None
    require(result.returncode == 0, "BOOTSTRAP_SCOPED_OPERATION_UNVERIFIED")


def safe_configuration() -> dict[str, str]:
    keys = (
        "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "OWNER_GITHUB_TOKEN",
    )
    data = {key: os.getenv(key, "").strip() for key in keys}
    require(all(data.values()), "BOOTSTRAP_SCOPED_OWNER_CREDENTIAL_MISSING")
    require(re.fullmatch(r"[a-fA-F0-9]{32}", data["CLOUDFLARE_ACCOUNT_ID"]) is not None,
            "BOOTSTRAP_CLOUDFLARE_ACCOUNT_UNVERIFIED")
    # Free-first is the existing Owner policy: account/provider failures and
    # Worker binding readback remain fail-closed. No paid-plan upgrade is made.
    # Arbitrary yes/no entitlement flags are not evidence of real availability.
    require(os.getenv("GITHUB_REPOSITORY") == RUNNER_REPOSITORY
            and os.getenv("GITHUB_REF") == "refs/heads/main"
            and os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch",
            "BOOTSTRAP_TRUSTED_OWNER_WORKFLOW_REQUIRED")
    require(not (LEGACY_NAMES & {BUCKET, IMAGE_WORKER, PDF_WORKER}),
            "BOOTSTRAP_LEGACY_IDENTITY_FORBIDDEN")
    return data


def source_identity(repo: Path, sha: str) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "BOOTSTRAP_SOURCE_SHA_INVALID")
    require(repo.is_dir() and not repo.is_symlink(), "BOOTSTRAP_SOURCE_ROOT_INVALID")
    try:
        actual = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BootstrapBlocked("BOOTSTRAP_SOURCE_READ_UNVERIFIED") from None
    require(actual.returncode == 0 and actual.stdout.strip() == sha,
            "BOOTSTRAP_SOURCE_IDENTITY_MISMATCH")
    for path in (
        "cloudflare/r2-upload-worker.js",
        "cloudflare/pdf-generator-worker/src/index.js",
        "cloudflare/pdf-generator-worker/package-lock.json",
    ):
        entry = repo / path
        require(entry.is_file() and not entry.is_symlink(),
                "BOOTSTRAP_REVIEWED_SOURCE_ENTRY_MISSING")


def api(url: str, token: str) -> object:
    require(url.startswith(ACCOUNT_HOST + "/"), "BOOTSTRAP_PROVIDER_SCOPE_INVALID")
    request = Request(
        url,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            require(response.status == 200, "BOOTSTRAP_PROVIDER_STATUS_UNVERIFIED")
            body = response.read(2_000_001)
            require(len(body) <= 2_000_000, "BOOTSTRAP_PROVIDER_READBACK_TOO_LARGE")
    except (HTTPError, URLError, OSError, TimeoutError):
        raise BootstrapBlocked("BOOTSTRAP_PROVIDER_READBACK_DENIED") from None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise BootstrapBlocked("BOOTSTRAP_PROVIDER_JSON_INVALID") from None
    require(isinstance(data, dict) and data.get("success") is True,
            "BOOTSTRAP_PROVIDER_READBACK_UNVERIFIED")
    return data


def github_secret_inventory(env: dict[str, str]) -> None:
    gh_env = dict(os.environ)
    gh_env["GH_TOKEN"] = env["OWNER_GITHUB_TOKEN"]
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{RUNNER_REPOSITORY}/actions/secrets",
             "--paginate", "--jq", ".secrets[].name"],
            capture_output=True, text=True, check=False, env=gh_env, timeout=35,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BootstrapBlocked("BOOTSTRAP_GITHUB_SECRET_READBACK_UNVERIFIED") from None
    require(result.returncode == 0, "BOOTSTRAP_GITHUB_SECRET_SCOPE_DENIED")
    existing = set(result.stdout.splitlines())
    require(not (existing & set(SECRET_NAMES)),
            "BOOTSTRAP_EXISTING_GITHUB_SECRET_REQUIRES_SEPARATE_REVIEW")


def cloudflare_inventory(env: dict[str, str]) -> str:
    account = env["CLOUDFLARE_ACCOUNT_ID"]
    token = env["CLOUDFLARE_API_TOKEN"]
    scripts_response = api(f"{ACCOUNT_HOST}/accounts/{account}/workers/scripts", token)
    require(isinstance(scripts_response, dict), "BOOTSTRAP_WORKER_LIST_UNVERIFIED")
    scripts = scripts_response.get("result")
    info = scripts_response.get("result_info")
    # Cloudflare's Workers scripts list is not paginated in its documented
    # response schema; unlike R2 it may have no result_info at all. Never
    # accept an explicit pagination indicator that announces hidden pages.
    require(
        isinstance(scripts, list)
        and (info is None or (isinstance(info, dict) and info.get("total_pages") == 1))
        and len(scripts) < 100,
        "BOOTSTRAP_WORKER_LIST_PAGINATED_OR_AMBIGUOUS",
    )
    names = {x.get("id") for x in scripts if isinstance(x, dict)}
    require(IMAGE_WORKER not in names and PDF_WORKER not in names,
            "BOOTSTRAP_TARGET_WORKER_ALREADY_EXISTS")
    bucket_response = api(f"{ACCOUNT_HOST}/accounts/{account}/r2/buckets", token)
    require(isinstance(bucket_response, dict)
            and isinstance(bucket_response.get("result"), dict),
            "BOOTSTRAP_BUCKET_LIST_UNVERIFIED")
    buckets = bucket_response["result"]
    require(isinstance(buckets.get("buckets"), list),
            "BOOTSTRAP_BUCKET_LIST_UNVERIFIED")
    # R2 pagination lives at response.result_info.cursor, not result.cursor.
    bucket_page = bucket_response.get("result_info")
    require(
        isinstance(bucket_page, dict)
        and not bucket_page.get("cursor")
        and bucket_page.get("total_count") == len(buckets["buckets"])
        and len(buckets["buckets"]) < 100,
        "BOOTSTRAP_BUCKET_LIST_PAGINATED_OR_AMBIGUOUS",
    )
    require(BUCKET not in {x.get("name") for x in buckets["buckets"]
                           if isinstance(x, dict)},
            "BOOTSTRAP_TARGET_BUCKET_ALREADY_EXISTS")
    sub_response = api(f"{ACCOUNT_HOST}/accounts/{account}/workers/subdomain", token)
    require(isinstance(sub_response, dict)
            and isinstance(sub_response.get("result"), dict),
            "BOOTSTRAP_WORKERS_SUBDOMAIN_UNVERIFIED")
    sub = sub_response["result"]
    domain = sub.get("subdomain")
    require(isinstance(domain, str)
            and re.fullmatch(r"[a-z0-9-]{3,63}", domain) is not None,
            "BOOTSTRAP_WORKER_HOST_UNVERIFIED")
    return domain


def worker_config(path: Path, repo: Path, *, image: bool) -> None:
    # Explicit Worker names; never use product PDF's legacy default wrangler.toml.
    if image:
        name = IMAGE_WORKER
        entry = repo / "cloudflare/r2-upload-worker.js"
        # iPhone builds WebP derivatives; only signed R2 storage is required.
        bindings = ("[[r2_buckets]]\nbinding = \"R2_BUCKET\"\n"
                    f"bucket_name = \"{BUCKET}\"\n")
    else:
        name = PDF_WORKER
        entry = repo / "cloudflare/pdf-generator-worker/src/index.js"
        bindings = ('[browser]\nbinding = "BROWSER"\n'
                    '[vars]\nWAFLOW_PDF_GENERATOR_TIMEOUT_MS = "30000"\n')
    path.write_text(
        f'name = "{name}"\n'
        f'main = {json.dumps(str(entry.resolve()))}\n'
        'workers_dev = true\n'
        'compatibility_date = "2026-05-01"\n'
        'compatibility_flags = ["nodejs_compat"]\n'
        + bindings,
        encoding="utf-8",
    )


def readback(env: dict[str, str]) -> None:
    # Reuse reviewed exact binding validator. It rejects plaintext Variable secrets.
    from wafl_r2_preview_gate import command_json, deployment_version, require_bindings
    wrangler_env = dict(os.environ)
    wrangler_env["CLOUDFLARE_API_TOKEN"] = env["CLOUDFLARE_API_TOKEN"]
    wrangler_env["CLOUDFLARE_ACCOUNT_ID"] = env["CLOUDFLARE_ACCOUNT_ID"]
    wrangler_env["CI"] = "true"
    wrangler_env["CLOUDFLARE_SEND_METRICS"] = "false"

    def view(name: str) -> dict:
        try:
            version = deployment_version(command_json(
                ["deployments", "list", "--name", name, "--json"], wrangler_env))
            value = command_json(
                ["versions", "view", version, "--name", name, "--json"], wrangler_env)
        except Exception:
            raise BootstrapBlocked("BOOTSTRAP_NEW_WORKER_READBACK_UNVERIFIED") from None
        require(isinstance(value, dict), "BOOTSTRAP_NEW_WORKER_READBACK_INVALID")
        return value

    try:
        require_bindings(view(IMAGE_WORKER), BUCKET)
    except Exception:
        raise BootstrapBlocked("BOOTSTRAP_IMAGE_BINDINGS_UNVERIFIED") from None
    pdf = view(PDF_WORKER)
    bindings = pdf.get("resources", {}).get("bindings")
    require(isinstance(bindings, list), "BOOTSTRAP_PDF_BINDINGS_UNVERIFIED")
    for name, kind in (("BROWSER", "browser"), ("WAFLOW_PDF_GENERATOR_TOKEN", "secret_text")):
        require(len([x for x in bindings if isinstance(x, dict)
                     and x.get("name") == name and x.get("type") == kind]) == 1,
                "BOOTSTRAP_PDF_BINDINGS_UNVERIFIED")


def install_secrets(env: dict[str, str], secrets_to_set: dict[str, str]) -> None:
    # Write only names proved absent. No secret value reaches argv/log/artifacts.
    gh_env = dict(os.environ)
    gh_env["GH_TOKEN"] = env["OWNER_GITHUB_TOKEN"]
    for key in SECRET_NAMES:
        command(["gh", "secret", "set", key, "--repo", RUNNER_REPOSITORY],
                env=gh_env, stdin=secrets_to_set[key], timeout=35)


def bootstrap(repo: Path, sha: str, summary: Path) -> None:
    source_identity(repo, sha)
    env = safe_configuration()
    domain = cloudflare_inventory(env)
    github_secret_inventory(env)
    # Unique non-reused keys, never derived from the previously exposed legacy var.
    image_key = secrets.token_urlsafe(48)
    pdf_key = secrets.token_urlsafe(48)
    vars_to_set = {
        "WAFL_R2_PREVIEW_WORKER_NAME": IMAGE_WORKER,
        "WAFL_R2_PREVIEW_BUCKET_NAME": BUCKET,
        "WAFL_R2_PREVIEW_WORKER_URL": f"https://{IMAGE_WORKER}.{domain}.workers.dev",
        "WAFL_R2_WORKER_UPLOAD_SECRET": image_key,
        "WAFL_PDF_DEV_WORKER_NAME": PDF_WORKER,
        "WAFL_PDF_DEV_WORKER_URL": f"https://{PDF_WORKER}.{domain}.workers.dev",
        "WAFL_PDF_GENERATOR_TOKEN": pdf_key,
        "WAFL_R2_SIGNING_PARITY_APPROVED": "yes",
    }
    cf_env = dict(os.environ)
    cf_env["CLOUDFLARE_API_TOKEN"] = env["CLOUDFLARE_API_TOKEN"]
    cf_env["CLOUDFLARE_ACCOUNT_ID"] = env["CLOUDFLARE_ACCOUNT_ID"]
    cf_env["CLOUDFLARE_SEND_METRICS"] = "false"
    cf_env["CI"] = "true"
    with tempfile.TemporaryDirectory(prefix="wafl-dev-bootstrap-") as temp:
        base = Path(temp)
        image_conf, pdf_conf = base / "image.toml", base / "pdf.toml"
        worker_config(image_conf, repo, image=True)
        worker_config(pdf_conf, repo, image=False)
        command(["wrangler", "deploy", "--config", str(image_conf), "--dry-run"],
                env=cf_env, cwd=repo)
        pdf_dir = repo / "cloudflare/pdf-generator-worker"
        command(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                env=cf_env, cwd=pdf_dir, timeout=240)
        command(["wrangler", "deploy", "--config", str(pdf_conf), "--dry-run"],
                env=cf_env, cwd=pdf_dir)
        # Secrets first: absent names were checked above. If GitHub denies writes,
        # NO Cloudflare resource has been created.
        install_secrets(env, vars_to_set)
        # Recheck names to reduce races before first provider mutation.
        cloudflare_inventory(env)
        command(["wrangler", "r2", "bucket", "create", BUCKET], env=cf_env)
        command(["wrangler", "deploy", "--config", str(image_conf)],
                env=cf_env, cwd=repo)
        command(["wrangler", "secret", "put", "R2_WORKER_UPLOAD_SECRET",
                 "--name", IMAGE_WORKER], env=cf_env, stdin=image_key)
        command(["wrangler", "deploy", "--config", str(pdf_conf)],
                env=cf_env, cwd=pdf_dir)
        command(["wrangler", "secret", "put", "WAFLOW_PDF_GENERATOR_TOKEN",
                 "--name", PDF_WORKER], env=cf_env, stdin=pdf_key)
    readback(env)
    with summary.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n## WAFL isolated Development media first setup\n"
            "- New private bucket: `wafl-media-dev`\n"
            "- New Worker identities: `wafl-r2-dev`, `wafl-pdf-dev`\n"
            "- New Worker Secret bindings and scoped Runner Secret handoff: PASS\n"
            "- Existing Workers/bucket and legacy signing key: NOT_MODIFIED\n"
            "- R2/PDF real byte transport and iPhone functions: NOT_RUN\n"
            "- Production product, Supabase, OTA, App Store: NOT_RUN\n"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    try:
        bootstrap(args.repo, args.source_sha, args.summary)
    except BootstrapBlocked as exc:
        print(f"::error::{exc}")
        return 1
    except Exception:
        print("::error::BOOTSTRAP_UNEXPECTED_UNVERIFIED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
