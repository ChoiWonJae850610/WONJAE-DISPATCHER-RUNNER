#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.supabase.com/v1/projects"
PROJECT_REF = "aqqarddfmdfyzeonfgwl"
MIGRATION_NAME = "wafl_stage4_post_apply_hardening"
EXPECTED_PRIOR_MIGRATION = "wafl_stage4_target_schema"
MIGRATION_PATH = "supabase/migrations/202610070001_wafl_stage4_post_apply_hardening.sql"

EXPECTED_INDEXES = {
    "wafl_company_members_approved_by_idx",
    "wafl_company_members_rejected_by_idx",
    "wafl_company_members_suspended_by_idx",
    "wafl_company_members_withdrawal_requested_by_idx",
    "wafl_company_members_withdrawn_by_idx",
    "wafl_member_permissions_granted_by_idx",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def request_json(
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
        f"{API_ROOT}/{PROJECT_REF}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=60) as response:
            data = response.read()
            return json.loads(data) if data else None
    except HTTPError as exc:
        fail(f"Supabase Management API rejected {path}: HTTP {exc.code}")
    except URLError:
        fail(f"Supabase Management API connection failed for {path}")
    except json.JSONDecodeError:
        fail(f"Supabase Management API returned invalid JSON for {path}")


def query(token: str, sql: str) -> list[dict[str, Any]]:
    value = request_json(
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


def safe_file(repo: Path, relative: str) -> Path:
    path = (repo / relative).resolve()
    if repo not in path.parents or not path.is_file():
        fail(f"provider source file missing or unsafe: {relative}")
    return path


def migration_ledger(token: str) -> list[dict[str, Any]]:
    return query(
        token,
        "select version::text as version, name::text as name "
        "from supabase_migrations.schema_migrations order by version;",
    )


def index_state(token: str) -> dict[str, Any]:
    rows = query(
        token,
        """
select
  exists(
    select 1 from pg_catalog.pg_constraint c
    join pg_catalog.pg_index i on i.indexrelid = c.conindid
    where c.conrelid = 'public.company_members'::pg_catalog.regclass
      and c.conname = 'company_members_company_id_user_id_key'
      and c.contype = 'u'
      and c.convalidated
      and not c.condeferrable
      and i.indisunique and i.indisvalid and i.indisready
  ) as retained_unique,
  exists(
    select 1 from pg_catalog.pg_class i
    join pg_catalog.pg_namespace n on n.oid = i.relnamespace
    where n.nspname = 'public'
      and i.relname = 'company_members_company_user_unique'
      and i.relkind = 'i'
  ) as duplicate_index,
  coalesce(array_agg(idx.relname order by idx.relname)
    filter (where idx.relname like 'wafl_%'), array[]::name[])::text[] as wafl_indexes
from pg_catalog.pg_index pi
join pg_catalog.pg_class tbl on tbl.oid = pi.indrelid
join pg_catalog.pg_namespace ns on ns.oid = tbl.relnamespace
join pg_catalog.pg_class idx on idx.oid = pi.indexrelid
where ns.nspname = 'public'
  and tbl.relname in ('company_members', 'member_permissions')
  and pi.indisvalid and pi.indisready;
""".strip(),
    )
    if len(rows) != 1:
        fail("WAFL post-apply index readback returned unexpected rows")
    row = rows[0]
    raw = row.get("wafl_indexes") or []
    if isinstance(raw, str):
        raw = [part for part in raw.strip("{}").split(",") if part]
    return {
        "retained_unique": bool(row.get("retained_unique")),
        "duplicate_index": bool(row.get("duplicate_index")),
        "wafl_indexes": {str(value) for value in raw},
    }


def schema_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"db_schema", "db_schemas", "schemas", "exposed_schemas"}:
                if isinstance(child, str):
                    found.extend(part.strip() for part in child.split(",") if part.strip())
                elif isinstance(child, list):
                    found.extend(str(part).strip() for part in child if str(part).strip())
            found.extend(schema_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(schema_values(child))
    return found


def advisor_summary(token: str, kind: str) -> dict[str, Any]:
    try:
        value = request_json(token, f"/advisors/{kind}")
    except SystemExit:
        return {"status": "UNAVAILABLE"}
    lints = value.get("lints") if isinstance(value, dict) else None
    if not isinstance(lints, list):
        result = value.get("result") if isinstance(value, dict) else None
        lints = result.get("lints") if isinstance(result, dict) else None
    return {
        "status": "PASS" if isinstance(lints, list) else "UNAVAILABLE",
        "count": len(lints) if isinstance(lints, list) else None,
    }


def run(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    token = os.environ.get("WAFL_SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        fail("WAFL_SUPABASE_ACCESS_TOKEN is not configured")

    migration = safe_file(repo, MIGRATION_PATH)
    before_ledger = migration_ledger(token)
    before_names = [str(row.get("name") or "") for row in before_ledger]
    before_state = index_state(token)

    already_applied = MIGRATION_NAME in before_names
    if already_applied:
        if not before_ledger or before_names[-1] != MIGRATION_NAME:
            fail("WAFL live migration history advanced beyond the registered gate")
    else:
        if not before_ledger or before_names[-1] != EXPECTED_PRIOR_MIGRATION:
            fail("WAFL live migration history does not end at the expected prior migration")
        if not before_state["retained_unique"] or not before_state["duplicate_index"]:
            fail(
                "WAFL hardening preflight catalog state does not match "
                "the registered source contract"
            )
        if EXPECTED_INDEXES.intersection(before_state["wafl_indexes"]):
            fail("WAFL hardening preflight found one or more target indexes already present")
        request_json(
            token,
            "/database/migrations",
            method="POST",
            payload={"name": MIGRATION_NAME, "query": migration.read_text(encoding="utf-8")},
        )

    after_ledger = migration_ledger(token)
    after_names = [str(row.get("name") or "") for row in after_ledger]
    if after_names.count(MIGRATION_NAME) != 1 or after_names[-1] != MIGRATION_NAME:
        fail("WAFL hardening migration is not the exact live ledger tip")

    after_state = index_state(token)
    if not after_state["retained_unique"]:
        fail("WAFL retained membership uniqueness is missing after hardening")
    if after_state["duplicate_index"]:
        fail("WAFL redundant standalone membership index still exists after hardening")
    missing = sorted(EXPECTED_INDEXES - after_state["wafl_indexes"])
    if missing:
        fail("WAFL hardening post-readback is missing expected indexes: " + ", ".join(missing))

    postgrest = request_json(token, "/postgrest")
    schemas = sorted(set(schema_values(postgrest)))
    if not schemas:
        fail("WAFL Data API exposed-schema configuration could not be read")
    if "public" not in schemas:
        fail("WAFL Data API configuration does not expose required public schema")
    if "wafl_private" in schemas:
        fail("WAFL private helper schema is exposed through the Data API")

    evidence = {
        "project": "WAFL",
        "source_sha": args.source_sha,
        "project_ref": PROJECT_REF,
        "migration_name": MIGRATION_NAME,
        "migration_version": str(after_ledger[-1].get("version") or ""),
        "migration_applied_now": not already_applied,
        "retained_unique": "PASS",
        "duplicate_index_removed": "PASS",
        "six_fk_covering_indexes": "PASS",
        "data_api_public_exposed": "PASS",
        "data_api_wafl_private_excluded": "PASS",
        "security_advisors": advisor_summary(token, "security"),
        "performance_advisors": advisor_summary(token, "performance"),
        "production": "NOT_RUN",
        "data_copy": "NOT_RUN",
        "identity_mapping": "NOT_RUN",
        "device_physical": "NOT_RUN",
    }
    Path(args.evidence).write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with Path(args.summary).open("a", encoding="utf-8") as handle:
        handle.write("## WAFL Development provider gate\n\n")
        handle.write(f"- source SHA: \`{args.source_sha}\`\n")
        handle.write(f"- migration: \`{MIGRATION_NAME}\`\n")
        handle.write("- migration apply/readback: \`PASS\`\n")
        handle.write("- retained membership unique constraint: \`PASS\`\n")
        handle.write("- redundant standalone index removed: \`PASS\`\n")
        handle.write("- six audit-FK covering indexes: \`PASS\`\n")
        handle.write("- Data API public exposed / wafl_private excluded: \`PASS\`\n")
        handle.write("- data copy / identity mapping / Production / device: \`NOT_RUN\`\n")


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--summary", required=True)
    return parser


if __name__ == "__main__":
    run(parser().parse_args())
