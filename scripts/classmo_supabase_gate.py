#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.supabase.com/v1/projects"


def fail(message: str) -> None:
    raise SystemExit(message)


def safe_file(repo: Path, value: str) -> Path:
    relative = Path(value)
    if not value or relative.is_absolute() or ".." in relative.parts:
        fail("unsafe provider source path")
    path = (repo / relative).resolve()
    if repo not in path.parents:
        fail("provider source path escapes product checkout")
    if not path.is_file():
        fail(f"provider source file missing: {value}")
    return path


def request_json(
    project_ref: str,
    token: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "WONJAE-provider-gate",
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{API_ROOT}/{project_ref}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=60) as response:
            data = response.read()
            if not data:
                return None
            return json.loads(data)
    except HTTPError as exc:
        fail(f"Supabase Management API rejected {path}: HTTP {exc.code}")
    except URLError:
        fail(f"Supabase Management API connection failed for {path}")
    except json.JSONDecodeError:
        fail(f"Supabase Management API returned invalid JSON for {path}")


def query(project_ref: str, token: str, sql: str) -> list[dict[str, Any]]:
    value = request_json(
        project_ref,
        token,
        "/database/query",
        method="POST",
        payload={"query": sql},
    )
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    if isinstance(value, dict) and isinstance(value.get("result"), list):
        rows = value["result"]
        if all(isinstance(row, dict) for row in rows):
            return rows
    fail("Supabase query result format is not recognized")


def optional_get(project_ref: str, token: str, path: str) -> dict[str, Any]:
    try:
        value = request_json(project_ref, token, path)
    except SystemExit:
        return {"status": "UNAVAILABLE"}
    if isinstance(value, list):
        return {"status": "PASS", "count": len(value)}
    if isinstance(value, dict):
        for key in ("advisors", "notices", "result"):
            items = value.get(key)
            if isinstance(items, list):
                return {"status": "PASS", "count": len(items)}
        return {"status": "PASS"}
    return {"status": "UNAVAILABLE"}


def manifest_hash(manifest: Path, migration: Path) -> str:
    matches = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == migration.name:
            matches.append(parts[0])
    if len(matches) != 1 or len(matches[0]) != 64:
        fail("migration checksum manifest entry is missing or ambiguous")
    actual = hashlib.sha256(migration.read_bytes()).hexdigest()
    if actual != matches[0]:
        fail("migration checksum does not match SHA256SUMS")
    return actual


def rows_by_name(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("name") == name]


def migration_ledger(project_ref: str, token: str) -> list[dict[str, Any]]:
    return query(
        project_ref,
        token,
        "select version::text as version, name::text as name "
        "from supabase_migrations.schema_migrations order by version;",
    )


def function_metadata(project_ref: str, token: str) -> dict[str, Any]:
    rows = query(
        project_ref,
        token,
        """
select
  p.proacl::text as acl,
  p.prosecdef as security_definer,
  p.provolatile::text as volatility,
  p.proconfig::text as config,
  pg_get_functiondef(p.oid) as definition
from pg_catalog.pg_proc p
where p.oid =
  'classmo_private.validate_course_configuration(uuid,uuid,jsonb)'::regprocedure;
""".strip(),
    )
    if len(rows) != 1:
        fail("validate_course_configuration live function identity is missing or ambiguous")
    return rows[0]


def require_nonproduction(project_ref: str, token: str) -> None:
    rows = query(
        project_ref,
        token,
        """
select
  count(*) filter (
    where environment in ('development','preview')
  )::int as allowed_count,
  count(*) filter (
    where environment not in ('development','preview')
  )::int as forbidden_count
from public.classmo_environment;
""".strip(),
    )
    if len(rows) != 1:
        fail("CLASSMO non-Production environment guard returned unexpected rows")
    allowed = int(rows[0].get("allowed_count") or 0)
    forbidden = int(rows[0].get("forbidden_count") or 0)
    if allowed < 1 or forbidden != 0:
        fail("CLASSMO non-Production environment guard failed")


def require_zero_residue(project_ref: str, token: str) -> None:
    rows = query(
        project_ref,
        token,
        """
select
  (select count(*) from auth.users
    where email like 'course-014-%@classmo.invalid')::int as auth_users,
  (select count(*) from public.workspaces
    where name like 'CLASSMO course 014 %')::int as workspaces,
  (select count(*) from public.command_receipts
    where idempotency_key like 'course-014:%')::int as receipts,
  (select count(*) from public.domain_events
    where actor_user_id in (
      'e0140000-0000-4000-8000-000000000001',
      'e0140000-0000-4000-8000-000000000002'
    ))::int as events;
""".strip(),
    )
    if len(rows) != 1:
        fail("CLASSMO zero-residue readback returned unexpected rows")
    if any(int(rows[0].get(key) or 0) != 0 for key in ("auth_users", "workspaces", "receipts", "events")):
        fail("CLASSMO course-operations runtime left synthetic residue")


def write_summary(path: Path, evidence: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write("## CLASSMO Development provider gate\n\n")
        handle.write(f"- source SHA: \`{evidence['source_sha']}\`\n")
        handle.write(f"- project ref: \`{evidence['project_ref']}\`\n")
        handle.write(f"- migration: \`{evidence['migration_name']}\`\n")
        handle.write(f"- migration version: \`{evidence['migration_version']}\`\n")
        handle.write(f"- migration applied now: \`{evidence['migration_applied_now']}\`\n")
        handle.write("- sequential runtime verification: \`PASS\`\n")
        handle.write("- cleanup / independent zero residue: \`PASS\`\n")
        handle.write("- independent concurrency verification: \`NOT_RUN\`\n")
        handle.write(
            f"- security advisor readback: \`{evidence['security_advisors']['status']}\`\n"
        )
        handle.write(
            f"- performance advisor readback: \`{evidence['performance_advisors']['status']}\`\n"
        )
        handle.write("- Production / EAS / OTA / device / physical: \`NOT_RUN\`\n")


def run(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    token = os.environ.get("CLASSMO_SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        fail("CLASSMO_SUPABASE_ACCESS_TOKEN is not configured")

    migration = safe_file(repo, args.migration_path)
    manifest = safe_file(repo, args.checksum_path)
    runtime = safe_file(repo, args.runtime_path)
    cleanup = safe_file(repo, args.cleanup_path)
    checksum = manifest_hash(manifest, migration)

    require_nonproduction(args.project_ref, token)
    before_ledger = migration_ledger(args.project_ref, token)
    target_before = rows_by_name(before_ledger, args.migration_name)
    before_meta = function_metadata(args.project_ref, token)

    if len(target_before) > 1:
        fail("target migration appears more than once in live history")
    migration_applied_now = False
    if target_before:
        if before_ledger[-1].get("name") != args.migration_name:
            fail("live migration history advanced beyond the registered provider gate")
    else:
        if not before_ledger or before_ledger[-1].get("name") != args.expected_prior_migration_name:
            fail("live migration history does not end at the expected prior migration")
        request_json(
            args.project_ref,
            token,
            "/database/migrations",
            method="POST",
            payload={
                "name": args.migration_name,
                "query": migration.read_text(encoding="utf-8"),
            },
        )
        migration_applied_now = True

    after_ledger = migration_ledger(args.project_ref, token)
    target_after = rows_by_name(after_ledger, args.migration_name)
    if len(target_after) != 1 or after_ledger[-1].get("name") != args.migration_name:
        fail("target migration was not recorded as the exact live ledger tip")
    migration_version = str(target_after[0].get("version") or "")
    if not migration_version:
        fail("target migration version readback is missing")

    after_meta = function_metadata(args.project_ref, token)
    for key in ("acl", "security_definer", "volatility", "config"):
        if before_meta.get(key) != after_meta.get(key):
            fail(f"migration changed validate_course_configuration metadata: {key}")
    definition = str(after_meta.get("definition") or "")
    if "option_entry.value->>'id'" not in definition:
        fail("installed function does not contain the migration-015 alias correction")

    query(args.project_ref, token, runtime.read_text(encoding="utf-8"))
    cleanup_rows = query(args.project_ref, token, cleanup.read_text(encoding="utf-8"))
    cleanup_pass = any(
        row.get("check_name") == "COURSE_ZERO_RESIDUE"
        and int(row.get("residue") or 0) == 0
        for row in cleanup_rows
    )
    if not cleanup_pass:
        fail("course-operations cleanup did not return COURSE_ZERO_RESIDUE = 0")
    require_zero_residue(args.project_ref, token)

    evidence = {
        "project": "CLASSMO",
        "project_ref": args.project_ref,
        "source_sha": args.source_sha,
        "migration_name": args.migration_name,
        "migration_version": migration_version,
        "migration_sha256": checksum,
        "migration_applied_now": migration_applied_now,
        "runtime": "PASS",
        "cleanup": "PASS",
        "zero_residue": "PASS",
        "concurrency": "NOT_RUN",
        "security_advisors": optional_get(
            args.project_ref,
            token,
            "/advisors/security",
        ),
        "performance_advisors": optional_get(
            args.project_ref,
            token,
            "/advisors/performance",
        ),
        "production": "NOT_RUN",
        "device_physical": "NOT_RUN",
    }
    Path(args.evidence).write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_summary(Path(args.summary), evidence)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--repo", required=True)
    value.add_argument("--project-ref", required=True)
    value.add_argument("--migration-name", required=True)
    value.add_argument("--migration-path", required=True)
    value.add_argument("--checksum-path", required=True)
    value.add_argument("--runtime-path", required=True)
    value.add_argument("--cleanup-path", required=True)
    value.add_argument("--expected-prior-migration-name", required=True)
    value.add_argument("--source-sha", required=True)
    value.add_argument("--evidence", required=True)
    value.add_argument("--summary", required=True)
    return value


if __name__ == "__main__":
    run(parser().parse_args())
