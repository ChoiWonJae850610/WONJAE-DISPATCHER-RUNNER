#!/usr/bin/env python3
"""No-write Cloudflare/GitHub credentials and name inventory for WAFL media Dev.

Only a separately authenticated SANJINWORKS Owner Provider gate on trusted
Runner/main can invoke this. PR validation uses synthetic offline fixtures only.
This tool does NOT attest Cloudflare billing, service entitlements, GitHub
Actions Secret write rights, or image/PDF runtime behavior.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

from wafl_development_media_bootstrap import (
    BootstrapBlocked,
    RUNNER_REPOSITORY,
    SECRET_NAMES,
    cloudflare_inventory,
    require,
    source_identity,
)

REQUIRED_INPUT_NAMES = (
    "OWNER_GITHUB_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "WAFL_CLOUDFLARE_ACCOUNT_ID",
    "WAFL_R2_IMAGES_ENTITLEMENT_APPROVED",
    "WAFL_PDF_BROWSER_ENTITLEMENT_APPROVED",
    "WAFL_MEDIA_DEV_BILLING_APPROVED",
)


def protected_runtime() -> dict[str, str]:
    require(
        os.getenv("GITHUB_REPOSITORY") == RUNNER_REPOSITORY
        and os.getenv("GITHUB_REF") == "refs/heads/main"
        and os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch",
        "MEDIA_READONLY_TRUSTED_OWNER_WORKFLOW_REQUIRED",
    )
    keys = ("OWNER_GITHUB_TOKEN", "CLOUDFLARE_API_TOKEN", "WAFL_CLOUDFLARE_ACCOUNT_ID")
    values = {name: os.getenv(name, "").strip() for name in keys}
    require(all(values.values()), "MEDIA_READONLY_REQUIRED_TOKEN_OR_ACCOUNT_ID_MISSING")
    require(
        re.fullmatch(r"[0-9a-fA-F]{32}", values["WAFL_CLOUDFLARE_ACCOUNT_ID"])
        is not None,
        "MEDIA_READONLY_ACCOUNT_ID_FORMAT_INVALID",
    )
    return values


def secret_metadata_read(token: str) -> set[str]:
    """GET metadata only; never fetch a secret value or perform a write."""
    env = dict(os.environ)
    env["GH_TOKEN"] = token
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "-X",
                "GET",
                f"repos/{RUNNER_REPOSITORY}/actions/secrets",
                "--paginate",
                "--jq",
                ".secrets[].name",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BootstrapBlocked("MEDIA_READONLY_GITHUB_SECRET_READ_UNAVAILABLE") from None
    require(result.returncode == 0, "MEDIA_READONLY_OWNER_TOKEN_SECRET_READ_DENIED")
    names = result.stdout.splitlines()
    require(len(names) == len(set(names)), "MEDIA_READONLY_SECRET_LIST_AMBIGUOUS")
    require(len(names) <= 500, "MEDIA_READONLY_SECRET_LIST_TOO_LARGE")
    require(
        all(re.fullmatch(r"[A-Z0-9_]{1,100}", name) for name in names),
        "MEDIA_READONLY_SECRET_NAME_INVALID",
    )
    return set(names)


def inspect(repo: Path, sha: str, summary: Path) -> None:
    source_identity(repo, sha)
    config = protected_runtime()
    existing = secret_metadata_read(config["OWNER_GITHUB_TOKEN"])
    collision = sorted(set(SECRET_NAMES) & existing)
    # Never manufacture approval from a screenshot, plan, or a Secret's mere
    # presence. Billing, Images and Browser authority are NOT_VERIFIED here.
    missing_names = sorted(set(REQUIRED_INPUT_NAMES) - existing)
    cloudflare_config = {
        "CLOUDFLARE_ACCOUNT_ID": config["WAFL_CLOUDFLARE_ACCOUNT_ID"],
        "CLOUDFLARE_API_TOKEN": config["CLOUDFLARE_API_TOKEN"],
    }
    try:
        cloudflare_inventory(cloudflare_config)  # only three authenticated GETs
        cloudflare_status = "PASS_NEW_NAMES_ABSENT"
    except BootstrapBlocked as exc:
        cloudflare_status = exc.args[0]
    collision_status = "BLOCKED" if collision else "PASS_NONE"
    inputs_status = "MISSING_NAMES" if missing_names else "PRESENT_NOT_VALUE_VERIFIED"
    lines = [
        "## WAFL isolated Development media — read-only account preflight",
        "- GitHub Actions Secrets metadata GET: PASS",
        "- Current trusted Owner token Secrets **read** access: PASS",
        "- Current trusted Owner token Secrets **write** access: NOT_VERIFIED (no write allowed)",
        f"- Exact new-only Runner Secret name collisions: {collision_status}",
        f"- Required input Secret *names* present: {inputs_status}",
        f"- Cloudflare account API and new Worker/R2 names: {cloudflare_status}",
        "- Cloudflare Images entitlement / actual Worker transform: NOT_VERIFIED",
        "- Cloudflare Browser Run entitlement / actual PDF bytes: NOT_VERIFIED",
        "- Account-specific billing/possible charges: NOT_VERIFIED",
        "- Worker/bucket create or deploy, Secrets write, PDF/image calls: NOT_RUN",
        "- Original Worker's objects, Production, DB, device, OTA: NOT_MODIFIED",
        "- Bootstrap Owner action remains NOT_AUTHORIZED by this read-only evidence",
    ]
    # Avoid exposing all Secret names, API response bodies, credential values,
    # host addresses or GitHub/Cloudflare user identity to Action summaries.
    with summary.open("a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
    require(not collision, "MEDIA_READONLY_NEW_SECRET_NAME_COLLISION")
    # Missing future approvals are a finding, not a reason to suppress useful
    # read-only Cloudflare account evidence or request their premature grant.
    require(
        cloudflare_status == "PASS_NEW_NAMES_ABSENT",
        "MEDIA_READONLY_CLOUDFLARE_UNVERIFIED_OR_COLLISION",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    try:
        inspect(args.repo, args.source_sha, args.summary)
    except BootstrapBlocked as error:
        print(f"::error::{error}")
        return 1
    except Exception:
        print("::error::MEDIA_READONLY_UNEXPECTED_UNVERIFIED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
