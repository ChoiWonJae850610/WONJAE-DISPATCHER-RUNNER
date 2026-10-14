#!/usr/bin/env python3
"""CLASSMO 017: exact Development private Storage bucket + schema, Owner-gated.

This program is called only by the trusted registered Provider workflow.
It does not upload image bytes or claim runtime, two-session, cleanup or device PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SPEC = importlib.util.spec_from_file_location(
    "registered_supabase_migration", Path(__file__).with_name("registered_supabase_migration.py")
)
assert SPEC and SPEC.loader
reg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reg)

Blocked = reg.Blocked
require = reg.require
REF = "hfclkbqzxiqhcckwknih"
BASELINE = "4ea17a3d1d55298f1c86ad113b81c870a5d24363"
PATH = "supabase/migrations/20261014001700_017_editorial_media.sql"
BLOB = "71c998b431986f340835437ea9a6f689bcbc1f3e"
MANIFEST = "supabase/migrations/SHA256SUMS"
MANIFEST_BLOB = "a54512272d88105630ad3ebdade7145e1acd4a71"
SQL_SHA256 = "04c65a4fae67b2fa8eedf0c9ef8759ddab71077b0235327d05ff1fa7fad03311"
PRIOR = "016_editorial_drafts"
NAME = "017_editorial_media"
BUCKET = "classmo-editorial"
MIMES = frozenset({"image/webp", "image/jpeg", "image/png"})
LIMIT = 5242880
TABLES = frozenset({
    "editorial_display_profiles", "editorial_media_slots",
    "editorial_media_assets", "editorial_media_history",
})
POLICIES = frozenset({"classmo_editorial_insert", "classmo_editorial_read"})

# The bucket DML is the ONLY trusted addition to the immutable 017 SQL.
# Supabase documents creating a bucket through an INSERT into storage.buckets.
BUCKET_DML = (
    "insert into storage.buckets (id,name,public,file_size_limit,allowed_mime_types) "
    "values ('classmo-editorial','classmo-editorial',false,5242880,"
    "array['image/webp','image/jpeg','image/png']::text[]);\n"
)
BUCKETS_SQL = (
    "select id,name,public,file_size_limit,allowed_mime_types "
    "from storage.buckets order by id;"
)
POLICIES_SQL = (
    "select policyname,cmd,roles::text as roles,coalesce(qual,'') as qual,"
    "coalesce(with_check,'') as with_check from pg_policies "
    "where schemaname='storage' and tablename='objects' order by policyname;"
)
TABLES_SQL = (
    "select c.relname as table_name,c.relrowsecurity as rls,"
    "c.relforcerowsecurity as force_rls from pg_class c "
    "join pg_namespace n on n.oid=c.relnamespace "
    "where n.nspname='public' and c.relname in "
    "('editorial_display_profiles','editorial_media_slots',"
    "'editorial_media_assets','editorial_media_history') order by c.relname;"
)
READS = frozenset({BUCKETS_SQL, POLICIES_SQL, TABLES_SQL})


class Client(reg.Client):
    """Strict metadata read-only and single migration API write."""

    def __init__(self, ref: str, token: str):
        require(ref == REF, "C017_PROJECT_INVALID")
        super().__init__(ref, token)

    def _request(self, path, payload=None):
        if path == "/database/query/read-only" and isinstance(payload, dict) and (
            payload.get("query") in READS and set(payload) == {"query"}
        ):
            req = Request(
                f"{reg.ROOT}/{self.ref}{path}",
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "CLASSMO-027P-Owner-Storage",
                },
            )
            try:
                with urlopen(req, timeout=60) as response:
                    return json.loads(response.read())
            except HTTPError as exc:
                raise Blocked(f"C017_READ_HTTP_{exc.code}") from None
            except (URLError, OSError, ValueError):
                raise Blocked("C017_READ_UNVERIFIED") from None
        return super()._request(path, payload)

    def read(self, query: str) -> list[dict]:
        require(query in READS, "C017_READ_QUERY_DENIED")
        result = self._request("/database/query/read-only", {"query": query})
        if isinstance(result, dict):
            result = result.get("result")
        require(isinstance(result, list) and all(isinstance(row, dict) for row in result),
                "C017_READ_SHAPE_UNVERIFIED")
        return result

    def apply(self, name: str, sql: str) -> None:
        require(name == NAME and sql.startswith(BUCKET_DML),
                "C017_MIGRATION_UNREVIEWED")
        super().apply(name, sql)


def pinned_source(repo: Path, sha: str) -> str:
    require(reg.SHA.fullmatch(sha) is not None, "C017_SOURCE_SHA_INVALID")
    reg.git(repo, "merge-base", "--is-ancestor", BASELINE, sha)
    sql = reg.source_sql(repo, sha, PATH, BLOB)
    require(hashlib.sha256(sql.encode("utf-8")).hexdigest() == SQL_SHA256,
            "C017_SQL_CHECKSUM_CHANGED")
    require(reg.git(repo, "rev-parse", f"{sha}:{MANIFEST}") == MANIFEST_BLOB,
            "C017_MANIFEST_CHANGED")
    manifest = (repo / MANIFEST).read_bytes()
    sha1 = hashlib.sha1(b"blob " + str(len(manifest)).encode() + b"\0" + manifest).hexdigest()
    require(sha1 == MANIFEST_BLOB, "C017_MANIFEST_BYTES_CHANGED")
    entries = manifest.decode("utf-8").strip().splitlines()
    require(len(entries) == 17 and entries[-1] == f"{SQL_SHA256}  {PATH.rsplit('/', 1)[-1]}",
            "C017_MANIFEST_017_UNVERIFIED")
    require(entries[-2].endswith("  20261012001600_016_editorial_drafts.sql"),
            "C017_PRIOR_MANIFEST_CHANGED")
    return sql


def verify_bucket(rows: list[dict]) -> None:
    require(len(rows) == 1, "C017_BUCKET_INVENTORY_UNVERIFIED")
    row = rows[0]
    require(row.get("id") == BUCKET and row.get("name") == BUCKET
            and row.get("public") is False
            and row.get("file_size_limit") == LIMIT
            and isinstance(row.get("allowed_mime_types"), list)
            and set(row["allowed_mime_types"]) == MIMES
            and len(row["allowed_mime_types"]) == len(MIMES),
            "C017_PRIVATE_BUCKET_SETTINGS_UNVERIFIED")


def verify_policies(rows: list[dict]) -> None:
    require(len(rows) == 2 and
            {row.get("policyname") for row in rows} == POLICIES,
            "C017_STORAGE_POLICIES_UNVERIFIED")
    for row in rows:
        roles = str(row.get("roles", "")).lower()
        require("authenticated" in roles and "anon" not in roles
                and "public" not in roles and
                "classmo_private.editorial_storage_allowed" in
                str(row.get("qual", "")) + str(row.get("with_check", "")),
                "C017_STORAGE_POLICY_BROAD")
        if row["policyname"] == "classmo_editorial_insert":
            require(row.get("cmd") == "INSERT" and "editorial_storage_allowed" in
                    str(row.get("with_check")), "C017_INSERT_POLICY_UNVERIFIED")
        else:
            require(row.get("cmd") == "SELECT" and "editorial_storage_allowed" in
                    str(row.get("qual")), "C017_READ_POLICY_UNVERIFIED")


def verify_tables(rows: list[dict]) -> None:
    require(len(rows) == 4 and {row.get("table_name") for row in rows} == TABLES
            and all(row.get("rls") is True and row.get("force_rls") is True
                    for row in rows), "C017_TABLE_RLS_UNVERIFIED")


def execute(client: Client, repo: Path, sha: str) -> dict[str, object]:
    # Source and immutable preflight MUST be verified before first provider mutation.
    sql = pinned_source(repo, sha)
    client.project()
    ledger_before = client.ledger()
    require(len(ledger_before) == 16 and ledger_before[-1][1] == PRIOR
            and NAME not in [name for _version, name in ledger_before],
            "C017_LEDGER_NOT_READY")
    require(client.read(BUCKETS_SQL) == [] and client.read(POLICIES_SQL) == []
            and client.read(TABLES_SQL) == [], "C017_PREFLIGHT_CHANGED")
    client.apply(NAME, BUCKET_DML + sql)
    ledger_after = client.ledger()
    require(len(ledger_after) == 17 and ledger_after[:-1] == ledger_before
            and ledger_after[-1][1] == NAME
            and ledger_after[-1][0] not in {v for v, _n in ledger_before},
            "C017_POST_LEDGER_UNVERIFIED")
    verify_bucket(client.read(BUCKETS_SQL))
    verify_policies(client.read(POLICIES_SQL))
    verify_tables(client.read(TABLES_SQL))
    return {
        "result": "C017_PRIVATE_STORAGE_SCHEMA_APPLIED_RUNTIME_NOT_VERIFIED",
        "source_sha": sha, "project_ref": client.ref,
        "migration_name": NAME, "migration_blob": BLOB,
        "migration_sha256": SQL_SHA256,
        "bucket": BUCKET, "public": False, "file_size_limit": LIMIT,
        "allowed_mime_types": sorted(MIMES), "migration_version": ledger_after[-1][0],
        "previous_migrations": 16, "table_rls": "VERIFIED",
        "storage_policies": "VERIFIED",
        "runtime_sql": "NOT_RUN", "independent_race": "NOT_RUN",
        "upload_bytes_mime_exif": "NOT_RUN", "synthetic_cleanup": "NOT_RUN",
        "public_delivery": "NOT_RUN", "production": "NOT_RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("repo", "source-sha", "project-ref", "migration-blob",
                 "manifest-blob", "summary", "evidence"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        require(args.project_ref == REF and args.migration_blob == BLOB
                and args.manifest_blob == MANIFEST_BLOB,
                "C017_REGISTERED_IDENTITY_CHANGED")
        result = execute(
            Client(args.project_ref, os.getenv("CLASSMO_SUPABASE_ACCESS_TOKEN", "")),
            Path(args.repo).resolve(), args.source_sha,
        )
        evidence = Path(args.evidence)
        evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
        evidence.chmod(0o600)
        with Path(args.summary).open("a", encoding="utf-8") as out:
            out.write(
                "\n## CLASSMO 017 Development private Storage schema\n"
                "- Migration and 16 prior ledger records: VERIFIED\n"
                "- Bucket: PRIVATE; MIME/5-MiB: VERIFIED\n"
                "- Storage policies and four RLS tables: VERIFIED\n"
                "- Runtime/race/bytes/cleanup/device: NOT_RUN\n"
            )
        return 0
    except Blocked as err:
        print(f"::error::{err}")
        return 1
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print("::error::C017_RESULT_UNVERIFIED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
