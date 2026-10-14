#!/usr/bin/env python3
"""WAFL G1-S partial apply: independently Owner-gated 006 ONLY.

No 004/005 replay, arbitrary SQL, user/identity writes, R2, EAS or Production.
Read-only API preflight includes actual PostgREST exposed schemas; SQL settings
alone never prove Data API isolation. The single write is a named 006 migration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from scripts import wafl_g1_supabase_gate as g1

Blocked = g1.GateBlocked
ensure = g1.ensure

PROJECT = "aqqarddfmdfyzeonfgwl"
PRODUCT = "WAFL Development"
BASELINE = "edbaba54a4c2d6eb924b610a2703e87c6484db67"
BLOBS = (
    "09b5cf566f5b38726a68877afee4fed498b56b13",
    "572938ff0109a405918ee4ff20d5212ae09b55c1",
    "9cd1929f2893b94c4aea38ba286cabd19c464c3b",
)
NAME = "wafl_cloud_rpc_006"
MIGRATION_PATH = "supabase/migrations/202610100003_wafl_cloud_rpc_006.sql"
MIGRATIONS = (
    ("20261004072559", "wafl_auth_tenant_foundation"),
    ("20261004091059", "wafl_auth_helper_private_schema"),
    ("20261006011325", "wafl_stage4_target_schema"),
    ("20261007040240", "wafl_stage4_post_apply_hardening"),
    ("20261013150807", "wafl_cloud_auth_004"),
    ("20261013150814", "wafl_cloud_domain_005"),
)
RPC_NAMES = (
    "wafl_can_read_assigned_work_order",
    "wafl_work_order_draft_command",
    "wafl_asset_metadata_command",
    "wafl_document_share_command",
)
RPC_PROFILES = frozenset({
    ("wafl_private", "wafl_can_read_assigned_work_order"),
    ("wafl_private", "wafl_work_order_draft_command"),
    ("wafl_private", "wafl_asset_metadata_command"),
    ("wafl_private", "wafl_document_share_command"),
    ("public", "wafl_work_order_draft_command"),
    ("public", "wafl_asset_metadata_command"),
    ("public", "wafl_document_share_command"),
})
POLICIES = frozenset({
    ("work_orders", "wafl_work_orders_assigned_read"),
    ("work_order_revisions", "wafl_revisions_assigned_read"),
})
PRIVATE_TABLES = frozenset({"asset_upload_intents", "document_share_proofs"})

# Every external query is a fixed metadata SELECT through Supabase's
# dedicated read-only endpoint. Only the inherited named G1 migration call
# is write-capable, and only after the immutable state checks below.
RPC_SQL = (
    "select n.nspname::text as schema_name,p.proname::text as name,"
    "pg_get_userbyid(p.proowner)::text as owner,p.prosecdef as definer,"
    "has_function_privilege('anon',p.oid,'EXECUTE') as anon_execute,"
    "has_function_privilege('authenticated',p.oid,'EXECUTE') as auth_execute "
    "from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace "
    "where n.nspname in ('public','wafl_private') and p.proname in "
    "('wafl_can_read_assigned_work_order','wafl_work_order_draft_command',"
    "'wafl_asset_metadata_command','wafl_document_share_command') "
    "order by n.nspname,p.proname;"
)
POLICIES_SQL = (
    "select tablename::text as table_name,policyname::text as name,"
    "cmd::text as cmd,roles::text as roles,coalesce(qual,'')::text as qual "
    "from pg_catalog.pg_policies where schemaname='public' and "
    "tablename in ('work_orders','work_order_revisions') "
    "order by tablename,policyname;"
)
PRIVATE_SQL = (
    "select c.relname::text as name,c.relrowsecurity as rls,"
    "c.relforcerowsecurity as forced,pg_get_userbyid(c.relowner)::text as owner "
    "from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace "
    "where n.nspname='wafl_private' and c.relkind='r' and c.relname in "
    "('asset_upload_intents','document_share_proofs') order by c.relname;"
)
GRANTS_SQL = (
    "select c.relname::text as name from pg_catalog.pg_class c "
    "join pg_catalog.pg_namespace n on n.oid=c.relnamespace "
    "where n.nspname='public' and c.relkind='r' and ("
    "has_table_privilege('anon',c.oid,'SELECT') or "
    "has_table_privilege('anon',c.oid,'INSERT') or "
    "has_table_privilege('anon',c.oid,'UPDATE') or "
    "has_table_privilege('anon',c.oid,'DELETE') or "
    "has_table_privilege('authenticated',c.oid,'SELECT') or "
    "has_table_privilege('authenticated',c.oid,'INSERT') or "
    "has_table_privilege('authenticated',c.oid,'UPDATE') or "
    "has_table_privilege('authenticated',c.oid,'DELETE')) "
    "order by c.relname;"
)
SCHEMA_SQL = (
    "select has_schema_privilege('anon','wafl_private','USAGE') as anon_usage,"
    "has_schema_privilege('authenticated','wafl_private','CREATE') as auth_create;"
)
ALLOWED_META = frozenset({RPC_SQL, POLICIES_SQL, PRIVATE_SQL, GRANTS_SQL, SCHEMA_SQL})


class Client(g1.Client):
    def __init__(self, project: str, token: str):
        ensure(project == PROJECT, "G1_006_TARGET_INVALID")
        # The original G1 mode also insists on independent admission and
        # a schema_apply_ready flag; no earlier G1 source apply runs here.
        super().__init__(project, token, mode="schema_apply", preflight_accepted=True)

    def query(self, sql: str) -> list[dict[str, Any]]:
        ensure(sql in ALLOWED_META or sql in g1.READ_QUERIES,
               "G1_006_SQL_READ_DENIED")
        result = self.api("/database/query/read-only", {"query": sql})
        if isinstance(result, dict):
            result = result.get("result")
        ensure(isinstance(result, list) and
               all(isinstance(row, dict) for row in result),
               "G1_006_READBACK_SHAPE_INVALID")
        return result

    def apply(self, name: str, sql: str) -> None:
        ensure(name == NAME and self.schema_apply_ready and
               hashlib.sha1(
                   b"blob " + str(len(sql.encode("utf-8"))).encode() +
                   b"\0" + sql.encode("utf-8")
               ).hexdigest() == BLOBS[2],
               "G1_006_ONLY_WRITE_DENIED")
        super().apply(name, sql)


def source(repo: Path, sha: str) -> tuple[str, set[str], set[str]]:
    ensure(g1.HEX.fullmatch(sha) is not None, "G1_006_SHA_INVALID")
    ensure(g1.git(repo, "rev-parse", "HEAD") == sha,
           "G1_006_SOURCE_HEAD_MOVED")
    g1.git(repo, "merge-base", "--is-ancestor", BASELINE, sha)
    for (_, rel), blob in zip(g1.MIGRATIONS, BLOBS, strict=True):
        path = repo / rel
        ensure(path.is_file() and not path.is_symlink()
               and path.resolve().is_relative_to(repo.resolve()),
               "G1_006_SOURCE_PATH_INVALID")
        ensure(g1.git(repo, "rev-parse", f"{sha}:{rel}") == blob
               and g1.blob_sha(path) == blob,
               "G1_006_SOURCE_BYTES_CHANGED")
    foundation: set[str] = set()
    for rel in g1.BASE_FILES:
        foundation.update(g1.TABLE.findall((repo / rel).read_text(encoding="utf-8")))
    domain = set(g1.TABLE.findall(
        (repo / g1.MIGRATIONS[1][1]).read_text(encoding="utf-8")
    ))
    ensure(len(foundation) == 11 and len(domain) == 51
           and not foundation.intersection(domain),
           "G1_006_SOURCE_CATALOG_CHANGED")
    # The immutable original 006 SQL is not amended by this executor.
    sql = (repo / MIGRATION_PATH).read_text(encoding="utf-8")
    ensure(sql.count("BEGIN;") >= 1 and sql.rstrip().endswith("COMMIT;")
           and "used_bytes + (CASE WHEN action = 'intent' THEN incoming_bytes ELSE 0 END)"
           in sql, "G1_006_SOURCE_SQL_SHAPE_INVALID")
    return sql, foundation, domain


def ledger(client: Client) -> list[tuple[str, str]]:
    rows = client.query(g1.LEDGER_SQL)
    result = []
    for row in rows:
        ensure(isinstance(row.get("version"), str) and
               isinstance(row.get("name"), str),
               "G1_006_LEDGER_SHAPE_INVALID")
        result.append((row["version"], row["name"]))
    ensure(len(result) == len(set(result)), "G1_006_LEDGER_DUPLICATE")
    return result


def tables(client: Client, expected: set[str]) -> None:
    actual = client.tables()
    ensure(set(actual) == expected, "G1_006_PUBLIC_CATALOG_DRIFT")
    ensure(all(row.get("rls") is True and row.get("forced") is True
               and row.get("owner") == "postgres" for row in actual.values()),
           "G1_006_RLS_OWNER_DRIFT")


def private_roadblock(client: Client, domain: set[str]) -> None:
    ensure(client.query(RPC_SQL) == [] and client.query(POLICIES_SQL) == []
           and client.query(PRIVATE_SQL) == [],
           "G1_006_PARTIAL_RPC_ALREADY_PRESENT")
    grants = client.query(GRANTS_SQL)
    ensure(all(isinstance(row.get("name"), str) for row in grants),
           "G1_006_GRANT_SHAPE_INVALID")
    ensure(not set(row["name"] for row in grants).intersection(domain),
           "G1_006_DOMAIN_GRANT_EXPANDED")
    config = client.query(SCHEMA_SQL)
    ensure(len(config) == 1 and config[0].get("anon_usage") is False
           and config[0].get("auth_create") is False,
           "G1_006_PRIVATE_SCHEMA_GRANT_DRIFT")


def verified_after(client: Client, expected_public: set[str]) -> dict[str, int]:
    tables(client, expected_public)
    config = client.query(SCHEMA_SQL)
    ensure(len(config) == 1 and config[0].get("anon_usage") is False
           and config[0].get("auth_create") is False,
           "G1_006_POST_PRIVATE_GRANT_DRIFT")
    private = client.query(PRIVATE_SQL)
    ensure(len(private) == 2 and {row.get("name") for row in private} == PRIVATE_TABLES
           and all(row.get("owner") == "postgres"
                   and row.get("rls") is True and row.get("forced") is True
                   for row in private), "G1_006_POST_PRIVATE_TABLE_UNVERIFIED")
    functions = client.query(RPC_SQL)
    ensure(len(functions) == len(RPC_PROFILES) and
           {(row.get("schema_name"), row.get("name")) for row in functions}
           == RPC_PROFILES and all(
               row.get("owner") == "postgres"
               and row.get("anon_execute") is False
               and row.get("auth_execute") is True
               and row.get("definer") is (row.get("schema_name") == "wafl_private")
               for row in functions
           ), "G1_006_POST_RPC_GRANTS_UNVERIFIED")
    policies = client.query(POLICIES_SQL)
    ensure(len(policies) == 2 and {
        (row.get("table_name"), row.get("name")) for row in policies
    } == POLICIES, "G1_006_POST_POLICIES_UNVERIFIED")
    for row in policies:
        ensure(row.get("cmd") == "SELECT" and
               "authenticated" in str(row.get("roles")) and
               "public" not in str(row.get("roles")).lower() and
               "anon" not in str(row.get("roles")).lower() and
               "wafl_can_read_assigned_work_order" in str(row.get("qual")),
               "G1_006_POST_POLICY_SCOPE_UNVERIFIED")
    return {"new_private_relations": len(private),
            "new_006_functions": len(functions),
            "new_assignee_policies": len(policies)}


def execute(client: Client, repo: Path, sha: str) -> dict[str, Any]:
    sql, foundation, domain = source(repo, sha)
    meta = client.api("")
    ensure(isinstance(meta, dict) and meta.get("id") == PROJECT
           and meta.get("name") == PRODUCT, "G1_006_PROJECT_UNVERIFIED")
    before = ledger(client)
    ensure(before == list(MIGRATIONS), "G1_006_LEDGER_NOT_EXACT_PARTIAL")
    # Must independently inspect the real Data API settings, not the DB
    # session configuration. Lack of permission/missing postgrest response
    # is a hard block before anything write-capable.
    client.exposure()
    ensure(set(client.exposed_schemas).issubset({"public", "graphql_public"}),
           "G1_006_DATA_API_EXPOSURE_UNSAFE")
    tables(client, foundation | domain)
    private_roadblock(client, domain)
    ensure(client.mode == "schema_apply" and client.preflight_accepted
           and client.schema_apply_ready is False,
           "G1_006_OLD_PROVIDER_PATH_INVALID")
    client.schema_apply_ready = True
    # Only this one write is admitted. The inherited API prefixes the
    # immutable SQL with transaction-local exact G1 006 attestation values.
    client.apply(NAME, sql)
    after = ledger(client)
    ensure(len(after) == 7 and after[:6] == before
           and after[-1][1] == NAME and after[-1][0] not in {x[0] for x in before},
           "G1_006_POST_LEDGER_UNVERIFIED")
    post = verified_after(client, foundation | domain)
    client.exposure()
    ensure(set(client.exposed_schemas).issubset({"public", "graphql_public"}),
           "G1_006_POST_DATA_API_EXPOSURE_UNSAFE")
    return {
        "result": "G1_006_ONLY_SCHEMA_APPLIED_NOT_G1_ACCEPT",
        "project": "WAFL", "environment": "development",
        "project_ref": PROJECT, "source_sha": sha,
        "sql_git_blob": BLOBS[2],
        "migration": NAME, "migration_version": after[-1][0],
        "installed_004_version": MIGRATIONS[4][0],
        "installed_005_version": MIGRATIONS[5][0],
        "public_relations": len(foundation | domain),
        **post,
        "applied_payload_hashes": client.applied_payload_hashes,
        "data_api_exposed_schemas": list(client.exposed_schemas),
        "data_copy": "NOT_RUN", "apple_binding": "NOT_RUN",
        "runtime_acl_rls": "NOT_RUN", "neon_invariants_closed": 0,
        "r2_images": "NOT_RUN", "pdf": "NOT_RUN",
        "eas_ota": "NOT_RUN", "production": "NOT_RUN",
        "physical_qa": "NOT_RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for key in ("repo", "source-sha", "project-ref", "source-baseline-sha",
                "migration-004-blob", "migration-005-blob",
                "migration-006-blob", "summary", "evidence"):
        parser.add_argument("--" + key, required=True)
    args = parser.parse_args()
    try:
        ensure(args.project_ref == PROJECT
               and args.source_baseline_sha == BASELINE
               and (args.migration_004_blob, args.migration_005_blob,
                    args.migration_006_blob) == BLOBS,
               "G1_006_REGISTRATION_MISMATCH")
        data = execute(
            Client(args.project_ref, os.getenv("WAFL_SUPABASE_ACCESS_TOKEN", "")),
            Path(args.repo).resolve(), args.source_sha,
        )
        receipt = Path(args.evidence)
        receipt.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
        receipt.chmod(0o600)
        with Path(args.summary).open("a", encoding="utf-8") as summary:
            summary.write(
                "\n## WAFL G1-S 006-only Development partial repair\n"
                "- 004/005 historical installation: PRESERVED\n"
                "- New 006 ledger, RPC/Policy readback: VERIFIED\n"
                "- Target/Data API/Owner/RLS: VERIFIED\n"
                "- Apple/Neon 50 invariants/R2/PDF/device: NOT_RUN\n"
            )
        return 0
    except Blocked as exc:
        print(f"::error::{exc}")
        return 1
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        print("::error::G1_006_RESULT_UNVERIFIED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
