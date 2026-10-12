#!/usr/bin/env python3
"""CLASSMO 016 Development: exact schema apply with read-only before/after guards.

The G1-S Owner click authorizes only the forward 016 DDL. Independent psql
runtime, multi-session race, synthetic fixtures, and cleanup remain G1-V NOT_RUN.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from scripts import classmo_editorial_016_preflight as g1
except ModuleNotFoundError:
    import classmo_editorial_016_preflight as g1

MIGRATION_NAME = "016_editorial_drafts"
POST_TABLE = (
    "select c.relname::text as name, c.relrowsecurity as rls, "
    "c.relforcerowsecurity as forced, pg_catalog.pg_get_userbyid(c.relowner) as owner, "
    "pg_catalog.has_table_privilege('anon', c.oid, 'SELECT') as anon_select, "
    "pg_catalog.has_table_privilege('authenticated', c.oid, 'SELECT') as auth_select, "
    "pg_catalog.has_table_privilege('authenticated', c.oid, 'INSERT') as auth_insert "
    "from pg_catalog.pg_class c join pg_catalog.pg_namespace n "
    "on n.oid=c.relnamespace where n.nspname='public' "
    "and c.relname='class_page_drafts' and c.relkind='r';"
)
POST_RPC = (
    "select p.proname::text as name, p.prosecdef as security_definer, "
    "pg_catalog.pg_get_userbyid(p.proowner) as owner, "
    "p.proconfig::text as config, "
    "pg_catalog.has_function_privilege('authenticated',p.oid,'EXECUTE') as auth_exec, "
    "pg_catalog.has_function_privilege('anon',p.oid,'EXECUTE') as anon_exec "
    "from pg_catalog.pg_proc p join pg_catalog.pg_namespace n "
    "on n.oid=p.pronamespace where n.nspname='public' and "
    "p.oid in ('public.read_editorial_draft(uuid,uuid)'::regprocedure, "
    "'public.save_editorial_draft(uuid,uuid,integer,jsonb,text)'::regprocedure) "
    "order by p.proname;"
)
POST_READS = frozenset((POST_TABLE, POST_RPC))


class ApplyClient(g1.Client):
    """Dedicated bounded mutation. Preflight Client remains strictly read-only."""

    def _post(self, path: str, payload: dict[str, str]) -> object:
        g1.require(
            (path == "/database/migrations" and
             set(payload) == {"name", "query"} and
             payload["name"] == MIGRATION_NAME and payload["query"])
            or (path == "/database/query/read-only" and
                set(payload) == {"query"} and payload["query"] in POST_READS),
            "C016_UNREVIEWED_WRITE_OR_SQL",
        )
        req = Request(
            g1.ROOT + self.ref + path, data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "CLASSMO-016-owner-schema",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=60) as response:
                data = response.read()
                return json.loads(data) if data else None
        except HTTPError as exc:
            operation = "migration_apply" if path == "/database/migrations" else "post_readback"
            raise g1.Blocked(f"C016_PROVIDER_HTTP_{exc.code} stage={operation}") from None
        except (URLError, OSError, ValueError):
            operation = "migration_apply" if path == "/database/migrations" else "post_readback"
            raise g1.Blocked(f"C016_PROVIDER_UNVERIFIED stage={operation}") from None

    def apply(self, sql: str) -> None:
        g1.require(bool(sql.strip()), "C016_EMPTY_MIGRATION")
        self._post("/database/migrations", {"name": MIGRATION_NAME, "query": sql})

    def post_read(self, sql: str) -> list[dict[str, object]]:
        g1.require(sql in POST_READS, "C016_POST_SQL_NOT_ALLOWED")
        value = self._post("/database/query/read-only", {"query": sql})
        if isinstance(value, dict):
            value = value.get("result")
        g1.require(isinstance(value, list) and
                   all(isinstance(row, dict) for row in value),
                   "C016_POST_READBACK_SHAPE")
        return value


def verify_schema_readback(client: ApplyClient) -> None:
    table = client.post_read(POST_TABLE)
    g1.require(len(table) == 1, "C016_DRAFT_TABLE_NOT_VERIFIED")
    t = table[0]
    g1.require(t.get("name") == "class_page_drafts" and
               t.get("owner") == "postgres" and
               t.get("rls") is True and t.get("forced") is True and
               t.get("anon_select") is False and
               t.get("auth_select") is False and t.get("auth_insert") is False,
               "C016_DRAFT_RLS_OR_GRANTS_UNVERIFIED")
    rpcs = client.post_read(POST_RPC)
    g1.require(len(rpcs) == 2 and
               {x.get("name") for x in rpcs} ==
               {"read_editorial_draft", "save_editorial_draft"},
               "C016_RPC_SET_UNVERIFIED")
    g1.require(all(
        x.get("owner") == "postgres" and
        x.get("security_definer") is True and
        x.get("auth_exec") is True and
        x.get("anon_exec") is False and
        isinstance(x.get("config"), str) and
        "search_path=" in x["config"] and
        "classmo_private" in x["config"] for x in rpcs
    ), "C016_RPC_GRANTS_UNVERIFIED")


def execute(client: ApplyClient, repo: Path, sha: str, blob: str) -> dict[str, object]:
    # Re-read project, env, all 15 historical ledger entries (including pinned
    # version 20260930001200 for the 012 alias), and absent draft catalog.
    preflight = g1.execute(client, repo, sha, blob)
    before = client.read(g1.LEDGER)
    g1.require(len(before) == 15, "C016_LEDGER_NOT_READY")
    prior_names, checksum = g1.verify(repo, sha, blob)
    g1.verify_prior_ledger(before, prior_names)

    original = (repo / g1.MIGRATION).read_bytes()
    g1.require(hashlib.sha256(original).hexdigest() == checksum,
               "C016_MIGRATION_BYTES_CHANGED")
    # The original Git blob is submitted as-is; no SQL rewriting or psql
    # meta-command execution, no privileged generic SQL fallback.
    try:
        sql = original.decode("utf-8")
    except UnicodeError:
        raise g1.Blocked("C016_MIGRATION_ENCODING_INVALID") from None
    g1.require("class_page_drafts" in sql and
               "create function public.save_editorial_draft" in sql.lower(),
               "C016_MIGRATION_SCOPE_CHANGED")
    client.apply(sql)

    after = client.read(g1.LEDGER)
    g1.require(len(after) == 16 and
               after[-1].get("name") == MIGRATION_NAME and
               bool(after[-1].get("version")) and
               after[:-1] == before, "C016_POST_LEDGER_UNVERIFIED")
    verify_schema_readback(client)
    return {
        "result": "C016_SCHEMA_APPLIED_NOT_RUNTIME_ACCEPTED",
        "project": "CLASSMO",
        "source_sha": sha,
        "migration_blob": blob,
        "migration_sha256": checksum,
        "migration_version": str(after[-1]["version"]),
        "ledger_001_015": preflight["ledger_001_015"],
        "draft_table_rls": "VERIFIED",
        "private_rpc_grants": "VERIFIED",
        "psql_runtime": "NOT_RUN",
        "race": "NOT_RUN",
        "synthetic_cleanup": "NOT_RUN",
        "production": "NOT_RUN",
        "device": "NOT_RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for arg in ("repo", "source-sha", "project-ref", "migration-blob",
                "summary", "evidence"):
        parser.add_argument("--" + arg, required=True)
    args = parser.parse_args()
    try:
        result = execute(
            ApplyClient(args.project_ref,
                        os.getenv("CLASSMO_SUPABASE_ACCESS_TOKEN", "")),
            Path(args.repo).resolve(), args.source_sha, args.migration_blob,
        )
    except g1.Blocked as error:
        print("CLASSMO_016_SCHEMA_BLOCKED code=" + str(error), flush=True)
        return 1
    Path(args.evidence).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    with Path(args.summary).open("a") as summary:
        summary.write("## CLASSMO 016 Development schema apply\n\n")
        for key, value in result.items():
            summary.write("- " + key + ": " + str(value) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
