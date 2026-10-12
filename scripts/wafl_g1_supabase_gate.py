#!/usr/bin/env python3
"""G1 guarded WAFL Development preflight and separately approved SQL source apply.

No data copy, Auth/identity-map writer, Cloudflare, EAS or Production operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = "https://api.supabase.com/v1/projects"
BASE_NAMES = (
    "wafl_auth_tenant_foundation",
    "wafl_auth_helper_private_schema",
    "wafl_stage4_target_schema",
    "wafl_stage4_post_apply_hardening",
)
MIGRATIONS = (
    ("wafl_cloud_auth_004", "supabase/migrations/202610100001_wafl_cloud_auth_004.sql"),
    ("wafl_cloud_domain_005", "supabase/migrations/202610100002_wafl_cloud_domain_005.sql"),
    ("wafl_cloud_rpc_006", "supabase/migrations/202610100003_wafl_cloud_rpc_006.sql"),
)
BASE_FILES = (
    "supabase/migrations/202610040001_wafl_auth_tenant_foundation.sql",
    "supabase/migrations/202610060001_wafl_stage4_target_schema.sql",
)
HEX = re.compile(r"^[a-f0-9]{40}$")
TABLE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?public\.([a-z][a-z0-9_]*)", re.I)


class GateBlocked(Exception):
    """Never expose raw external response bodies."""


def ensure(value: bool, code: str) -> None:
    if not value:
        raise GateBlocked(code)


def git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=False,
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise GateBlocked("G1_GIT_READ_UNAVAILABLE") from None
    ensure(result.returncode == 0, "G1_GIT_IDENTITY_MISMATCH")
    return result.stdout.strip()


def blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def verify_source(
    repo: Path, sha: str, baseline: str, blobs: tuple[str, ...]
) -> tuple[set[str], set[str]]:
    ensure(HEX.fullmatch(sha) is not None and HEX.fullmatch(baseline) is not None
           and len(blobs) == 3 and all(HEX.fullmatch(v) for v in blobs),
           "G1_SOURCE_IDENTITY_INVALID")
    ensure(git(repo, "rev-parse", "HEAD") == sha, "G1_SOURCE_HEAD_CHANGED")
    git(repo, "merge-base", "--is-ancestor", baseline, sha)
    for (_, rel), blob in zip(MIGRATIONS, blobs, strict=True):
        path = repo / rel
        ensure(path.is_file() and not path.is_symlink()
               and path.resolve().is_relative_to(repo.resolve()),
               "G1_SOURCE_PATH_UNSAFE")
        ensure(git(repo, "rev-parse", f"{baseline}:{rel}") == blob
               and blob_sha(path) == blob, "G1_MIGRATION_BYTES_CHANGED")
    foundation: set[str] = set()
    for rel in BASE_FILES:
        foundation.update(TABLE.findall((repo / rel).read_text(encoding="utf-8")))
    domain = set(TABLE.findall((repo / MIGRATIONS[1][1]).read_text(encoding="utf-8")))
    ensure(len(foundation) == 11 and len(domain) == 51 and not foundation.intersection(domain),
           "G1_SOURCE_CATALOG_INVALID")
    inventory_file = repo / "supabase/stage4/live-catalog-readback-2026-10-05.json"
    inventory = json.loads(inventory_file.read_text(encoding="utf-8"))[
        "source"]["notValidConstraints"]["inventory"]
    ensure(len(inventory) == 50
           and sum(item.get("contype") == "f" for item in inventory) == 46
           and sum(item.get("contype") == "c" for item in inventory) == 4,
           "G1_50_INVARIANTS_INVALID")
    return foundation, domain


class Client:
    def __init__(self, project: str, token: str):
        ensure(re.fullmatch(r"[a-z]{20}", project) is not None, "G1_PROJECT_REF_INVALID")
        ensure(bool(token), "G1_CREDENTIAL_MISSING")
        self.project = project
        self.token = token

    def api(self, path: str, payload: dict[str, str] | None = None) -> Any:
        ensure(path in ("", "/postgrest", "/database/query", "/database/migrations"),
               "G1_ENDPOINT_NOT_ALLOWED")
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": "Bearer " + self.token,
                   "Accept": "application/json", "User-Agent": "WAFL-G1-Owner"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            request = Request(
                f"{ROOT}/{self.project}{path}", data=body, headers=headers,
                method="POST" if body is not None else "GET",
            )
            with urlopen(request, timeout=60) as response:
                data = response.read()
                return json.loads(data) if data else None
        except HTTPError as exc:
            raise GateBlocked(f"G1_PROVIDER_HTTP_{exc.code}") from None
        except (URLError, OSError, ValueError):
            raise GateBlocked("G1_PROVIDER_TRANSPORT_UNVERIFIED") from None

    def query(self, sql: str) -> list[dict[str, Any]]:
        ensure(sql.lower().startswith("select ") and sql.count(";") <= 1,
               "G1_READONLY_SQL_REQUIRED")
        result = self.api("/database/query", {"query": sql})
        if isinstance(result, dict):
            result = result.get("result")
        ensure(isinstance(result, list) and all(isinstance(row, dict) for row in result),
               "G1_QUERY_SHAPE_UNVERIFIED")
        return result

    def ledger(self) -> list[str]:
        rows = self.query(
            "select version::text as version, name::text as name "
            "from supabase_migrations.schema_migrations order by version;"
        )
        names = [row.get("name") for row in rows]
        ensure(all(isinstance(name, str) for name in names)
               and len(names) == len(set(names)), "G1_LEDGER_INVALID")
        return names

    def exposure(self) -> None:
        response = self.api("/postgrest")
        found: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ("db_schema", "db_schemas", "schemas", "exposed_schemas"):
                        if isinstance(child, str):
                            found.extend(part.strip() for part in child.split(","))
                        elif isinstance(child, list):
                            found.extend(part for part in child if isinstance(part, str))
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(response)
        ensure("public" in found and "wafl_private" not in found,
               "G1_DATA_API_EXPOSURE_UNVERIFIED")

    def tables(self) -> dict[str, dict[str, Any]]:
        rows = self.query(
            "select c.relname::text as name, c.relrowsecurity as rls, "
            "c.relforcerowsecurity as forced, pg_catalog.pg_get_userbyid(c.relowner) as owner "
            "from pg_catalog.pg_class c join pg_catalog.pg_namespace n "
            "on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' "
            "order by c.relname;"
        )
        result = {}
        for row in rows:
            name = row.get("name")
            ensure(isinstance(name, str) and name not in result, "G1_TABLE_SHAPE_INVALID")
            result[name] = row
        return result

    def counts(self) -> dict[str, int]:
        rows = self.query(
            "select (select count(*) from auth.users)::int as users, "
            "(select count(*) from auth.identities where provider='apple')::int as apple, "
            "(select count(*) from public.wafl_auth_identity_map)::int as bridge;"
        )
        ensure(len(rows) == 1, "G1_AUTH_COUNTS_UNVERIFIED")
        result = {}
        for field in ("users", "apple", "bridge"):
            try:
                value = int(rows[0][field])
            except (KeyError, TypeError, ValueError):
                raise GateBlocked("G1_AUTH_COUNTS_UNVERIFIED") from None
            ensure(0 <= value < 100000000, "G1_AUTH_COUNTS_UNVERIFIED")
            result[field] = value
        return result

    def apply(self, name: str, sql: str) -> None:
        ensure(name in (item[0] for item in MIGRATIONS)
               and bool(sql.strip()), "G1_MIGRATION_UNKNOWN")
        self.api("/database/migrations", {"name": name, "query": sql})


def preflight(client: Client, foundation: set[str], domain: set[str]) -> dict[str, Any]:
    meta = client.api("")
    ensure(isinstance(meta, dict) and meta.get("id") == client.project
           and meta.get("name") == "WAFL Development",
           "G1_TARGET_IDENTITY_UNVERIFIED")
    ensure(client.ledger() == list(BASE_NAMES), "G1_LEDGER_DRIFT_OR_PARTIAL_APPLY")
    client.exposure()
    tables = client.tables()
    ensure(set(tables) == foundation and not set(tables).intersection(domain),
           "G1_FOUNDATION_OR_DOMAIN_DRIFT")
    ensure(all(r.get("owner") == "postgres" and r.get("rls") is True
               and r.get("forced") is True for r in tables.values()),
           "G1_FOUNDATION_RLS_OWNER_UNVERIFIED")
    return {"foundation_tables": len(foundation), "new_domain_relations_not_applied": 51,
            "deferred_neon_constraints": 50, "auth_counts": client.counts(),
            "neon_data_parity": "NOT_RUN", "apple_subject_equality": "NOT_VERIFIED"}


def execute(client: Client, repo: Path, sha: str, baseline: str,
            blobs: tuple[str, ...], mode: str, accepted: bool) -> dict[str, Any]:
    foundation, domain = verify_source(repo, sha, baseline, blobs)
    info = preflight(client, foundation, domain)
    if mode == "read_only":
        return {"result": "G1_R_READONLY_PREFLIGHT_PASS_NOT_G1_ACCEPT",
                **info, "schema_apply": "NOT_RUN", "identity_mapping": "NOT_RUN"}
    ensure(mode == "schema_apply" and accepted, "G1_S_REQUIRES_INDEPENDENT_PREFLIGHT_APPROVAL")
    names = list(BASE_NAMES)
    for migration, rel in MIGRATIONS:
        client.apply(migration, (repo / rel).read_text(encoding="utf-8"))
        names.append(migration)
        ensure(client.ledger() == names, "G1_S_MIGRATION_LEDGER_UNVERIFIED")
    ensure(set(client.tables()) == foundation | domain, "G1_S_DOMAIN_SCHEMA_UNVERIFIED")
    client.exposure()
    return {"result": "G1_S_SCHEMA_APPLIED_NOT_G1_ACCEPT",
            "migration_count": len(MIGRATIONS), "domain_relations": len(domain),
            "data_copy": "NOT_RUN", "constraints_validated": 0,
            "identity_mapping": "NOT_RUN", "runtime_rls": "NOT_RUN"}


def main() -> None:
    p = argparse.ArgumentParser()
    for key in ("repo", "source-sha", "source-baseline-sha", "project-ref",
                "migration-004-blob", "migration-005-blob", "migration-006-blob",
                "summary", "evidence"):
        p.add_argument("--" + key, required=True)
    p.add_argument("--mode", choices=("read_only", "schema_apply"), required=True)
    p.add_argument("--preflight-accepted", choices=("true", "false"), required=True)
    args = p.parse_args()
    try:
        info = execute(
            Client(args.project_ref, os.getenv("WAFL_SUPABASE_ACCESS_TOKEN", "")),
            Path(args.repo).resolve(), args.source_sha, args.source_baseline_sha,
            (args.migration_004_blob, args.migration_005_blob, args.migration_006_blob),
            args.mode, args.preflight_accepted == "true",
        )
        safe = {"project": "WAFL", "source_sha": args.source_sha, "mode": args.mode, **info}
        Path(args.evidence).write_text(json.dumps(safe, sort_keys=True) + "\n", encoding="utf-8")
        with Path(args.summary).open("a", encoding="utf-8") as stream:
            stream.write("WAFL G1 guarded Development execution\n\n")
            stream.write("Mode: " + args.mode + "\n")
            stream.write("Source SHA: " + args.source_sha + "\n")
            stream.write("Result: " + info["result"] + "\n")
            stream.write("Runtime/data/Apple acceptance, R2/PDF, EAS/Production/device: NOT_RUN\n")
    except GateBlocked as exc:
        print("::error::WAFL G1 blocked: " + str(exc))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
