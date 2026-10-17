#!/usr/bin/env python3
"""Two exact Development read-only admission checks, not live acceptance.

Runs only within the existing authenticated SANJINWORKS Owner Provider gate.
No product source execution, JWT minting, live login, DDL/DML or Provider writes.
Results describe infrastructure readiness, not real image/Apple/RLS acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECTS = {
    "CLASSMO": {
        "gate": "editorial_027_p2_readonly",
        "ref": "hfclkbqzxiqhcckwknih",
        "name": "CLASSMO Development",
        "plan": "supabase/tests/editorial_media_p2_plan.json",
    },
    "WAFL": {
        "gate": "g1v_auth_db_readonly",
        "ref": "aqqarddfmdfyzeonfgwl",
        "name": "WAFL Development",
        "plan": "supabase/tests/wafl_g1v_plan.json",
    },
}
BASE = "https://api.supabase.com/v1/projects/"
LEDGER = (
    "select name::text as name from supabase_migrations.schema_migrations "
    "order by version;"
)
CLASSMO_BUCKET = (
    "select id::text as id, public, file_size_limit::int as file_size_limit, "
    "allowed_mime_types from storage.buckets "
    "where id='classmo-editorial';"
)
WAFL_AUTH = (
    "select (select count(*)::int from auth.users) as users, "
    "(select count(*)::int from auth.identities where provider='apple') as apple, "
    "(select count(*)::int from public.wafl_auth_identity_map) as bridge;"
)
SQL = frozenset((LEDGER, CLASSMO_BUCKET, WAFL_AUTH))
WAFL_LEDGER = (
    "wafl_auth_tenant_foundation",
    "wafl_auth_helper_private_schema",
    "wafl_stage4_target_schema",
    "wafl_stage4_post_apply_hardening",
    "wafl_cloud_auth_004",
    "wafl_cloud_domain_005",
    "wafl_cloud_rpc_006",
)


class ReadinessBlocked(RuntimeError):
    """Fixed provider-free error only. Never propagate request or response bodies."""


def require(value: bool, code: str) -> None:
    if not value:
        raise ReadinessBlocked(code)


def git_read(repo: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ReadinessBlocked("READINESS_SOURCE_IDENTITY_UNAVAILABLE") from None


def local_contract(repo: Path, source_sha: str, project: str, plan_blob: str) -> None:
    config = PROJECTS[project]
    require(re.fullmatch(r"[a-f0-9]{40}", source_sha) is not None,
            "READINESS_SOURCE_SHA_INVALID")
    require(re.fullmatch(r"[a-f0-9]{40}", plan_blob) is not None,
            "READINESS_PLAN_BLOB_INVALID")
    require(git_read(repo, "rev-parse", "HEAD") == source_sha,
            "READINESS_SOURCE_HEAD_MOVED")
    # Only the immutable registered plan is read; do not execute product scripts.
    require(git_read(repo, "rev-parse", f"HEAD:{config['plan']}") == plan_blob,
            "READINESS_PLAN_BLOB_MOVED")
    plan = repo / config["plan"]
    require(plan.is_file() and not plan.is_symlink(),
            "READINESS_PLAN_PATH_INVALID")
    try:
        obj = json.loads(plan.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReadinessBlocked("READINESS_PLAN_INVALID") from None
    require(isinstance(obj, dict) and obj.get("project_ref") == config["ref"]
            if project == "CLASSMO"
            else isinstance(obj, dict) and obj.get("provider_contract", {}).get(
                "project_ref") == config["ref"], "READINESS_PLAN_PROJECT_MISMATCH")


class ReadOnlySupabase:
    def __init__(self, ref: str, token: str):
        require(re.fullmatch(r"[a-z]{20}", ref) is not None
                and bool(token), "READINESS_PROVIDER_CREDENTIAL_MISSING")
        self.ref = ref
        self.token = token

    def request(self, path: str, query: str | None = None):
        require(
            (path in ("", "/postgrest") and query is None)
            or (path == "/database/query/read-only" and query in SQL),
            "READINESS_ENDPOINT_OR_SQL_NOT_ALLOWED",
        )
        data = json.dumps({"query": query}).encode() if query else None
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "User-Agent": "WONJAE-development-readiness",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            req = Request(BASE + self.ref + path, data=data, headers=headers,
                          method="POST" if data else "GET")
            with urlopen(req, timeout=45) as response:
                return json.loads(response.read(2_000_001))
        except HTTPError as exc:
            raise ReadinessBlocked(f"READINESS_PROVIDER_HTTP_{exc.code}") from None
        except (URLError, OSError, ValueError):
            raise ReadinessBlocked("READINESS_PROVIDER_READ_UNVERIFIED") from None

    def read(self, sql: str) -> list[dict]:
        result = self.request("/database/query/read-only", sql)
        if isinstance(result, dict):
            result = result.get("result")
        require(isinstance(result, list) and all(isinstance(x, dict) for x in result),
                "READINESS_DATABASE_RESPONSE_UNVERIFIED")
        return result


def exposed_schemas(value) -> tuple[str, ...]:
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in ("db_schema", "db_schemas", "exposed_schemas", "schemas"):
                    if isinstance(child, str):
                        found.extend(x.strip() for x in child.split(","))
                    elif isinstance(child, list):
                        found.extend(x for x in child if isinstance(x, str))
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    require(bool(found) and "public" in found
            and all(re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name in found),
            "READINESS_DATA_API_SETTINGS_UNVERIFIED")
    return tuple(sorted(set(found)))


def inspect_readiness(project: str, client: ReadOnlySupabase) -> dict:
    config = PROJECTS[project]
    require(client.ref == config["ref"], "READINESS_TARGET_REF_MISMATCH")
    identity = client.request("")
    require(isinstance(identity, dict)
            and identity.get("id") == config["ref"]
            and identity.get("name") == config["name"],
            "READINESS_PROVIDER_PROJECT_NOT_VERIFIED")
    schemas = exposed_schemas(client.request("/postgrest"))
    ledger = client.read(LEDGER)
    names = [x.get("name") for x in ledger]
    require(all(isinstance(x, str) for x in names)
            and len(names) == len(set(names)),
            "READINESS_LEDGER_INVALID")
    if project == "CLASSMO":
        # Already-applied P1/017; neither SQL nor bucket is recreated.
        require("017_editorial_media" in names[-2:] or "editorial_media" in names[-2:],
                "READINESS_CLASSMO_017_LEDGER_MISSING")
        bucket = client.read(CLASSMO_BUCKET)
        require(len(bucket) == 1
                and bucket[0].get("id") == "classmo-editorial"
                and bucket[0].get("public") is False
                and int(bucket[0].get("file_size_limit") or 0) == 5 * 1024 * 1024
                and set(bucket[0].get("allowed_mime_types") or [])
                == {"image/png", "image/jpeg", "image/webp"},
                "READINESS_CLASSMO_PRIVATE_BUCKET_DRIFT")
        return {
            "result": "CLASSMO_P2_READONLY_PREFLIGHT_PASS_NOT_RUNTIME_ACCEPTANCE",
            "project": project,
            "private_bucket": "READBACK",
            "migration_017": "LEDGER_PRESENT",
            "data_api": "READBACK",
            "edge_deployment": "NOT_RUN",
            "authenticated_image_matrix": "NOT_RUN",
            "two_session_db_race": "NOT_RUN",
            "storage_cleanup": "NOT_RUN",
            "provider_mutations": 0,
        }
    require("wafl_private" not in schemas, "READINESS_WAFL_PRIVATE_SCHEMA_EXPOSED")
    require(tuple(names[-7:]) == WAFL_LEDGER,
            "READINESS_WAFL_007_LEDGER_DRIFT")
    counts = client.read(WAFL_AUTH)
    require(len(counts) == 1 and all(
        isinstance(counts[0].get(field), int) and
        0 <= counts[0][field] < 100_000_000
        for field in ("users", "apple", "bridge")
    ), "READINESS_WAFL_AUTH_COUNTS_UNVERIFIED")
    # Aggregates are non-secret liveness evidence, NOT two verified Apple
    # provider subjects or lawful Auth sessions.
    return {
        "result": "WAFL_G1V_READONLY_PREFLIGHT_PASS_NOT_APPLE_ACCEPTANCE",
        "project": project,
        "seven_migration_names": "MATCHED",
        "data_api_private_schema": "NOT_EXPOSED",
        "auth_user_count": counts[0]["users"],
        "apple_identity_count": counts[0]["apple"],
        "identity_bridge_count": counts[0]["bridge"],
        "two_distinct_apple_subjects": "NOT_VERIFIED",
        "authenticated_rpc_rls": "NOT_RUN",
        "two_session_race": "NOT_RUN",
        "neon_50_not_valid_parity": "NOT_RUN",
        "provider_mutations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for arg in ("project", "project-ref", "repo", "source-sha",
                "plan-blob", "summary", "evidence"):
        parser.add_argument("--" + arg, required=True)
    a = parser.parse_args()
    try:
        require(a.project in PROJECTS, "READINESS_PROJECT_NOT_ALLOWED")
        require(a.project_ref == PROJECTS[a.project]["ref"],
                "READINESS_TARGET_REF_MISMATCH")
        repo = Path(a.repo).resolve()
        local_contract(repo, a.source_sha, a.project, a.plan_blob)
        token = os.environ.get(a.project + "_SUPABASE_ACCESS_TOKEN", "")
        outcome = inspect_readiness(a.project, ReadOnlySupabase(a.project_ref, token))
        outcome["source_sha"] = a.source_sha
        outcome["plan_blob"] = a.plan_blob
    except ReadinessBlocked as exc:
        print("DEVELOPMENT_READINESS_BLOCKED code=" + str(exc), flush=True)
        return 1
    Path(a.evidence).write_text(json.dumps(outcome, sort_keys=True, indent=2) + "\n")
    with Path(a.summary).open("a", encoding="utf-8") as file:
        file.write("\n## Development readiness (read-only, no live acceptance)\n\n")
        for key, value in outcome.items():
            file.write(f"- {key}: {value}\n")
    print("DEVELOPMENT_READINESS_PREFLIGHT_PASS_NOT_LIVE_ACCEPTANCE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
