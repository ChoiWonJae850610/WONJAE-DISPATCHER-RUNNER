#!/usr/bin/env python3
"""Exact-blob, registered Development-only Supabase migration executor.

Only the authenticated SANJINWORKS Owner provider gate may call this script.
This module does not offer an arbitrary SQL endpoint, source writer or psql.
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
SHA = re.compile(r"[0-9a-f]{40}")
PROJECT = re.compile(r"[a-z]{20}")
MIGRATION = re.compile(r"[a-z][a-z0-9_]{0,99}")
SQL_PATH = re.compile(r"supabase/migrations/[0-9a-z][0-9a-z_-]*\.sql")
LEDGER = (
    "select version::text as version, name::text as name "
    "from supabase_migrations.schema_migrations order by version;"
)
MAX_SQL_BYTES = 512 * 1024


class Blocked(Exception):
    """Fixed, non-secret failure code for trusted Owner logs."""


def require(value: bool, reason: str) -> None:
    if not value:
        raise Blocked(reason)


def git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=False,
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("REGISTERED_MIGRATION_GIT_UNAVAILABLE") from None
    require(result.returncode == 0, "REGISTERED_MIGRATION_SOURCE_UNVERIFIED")
    return result.stdout.strip()


def source_sql(repo: Path, sha: str, path: str, blob: str) -> str:
    require(SHA.fullmatch(sha) is not None and SHA.fullmatch(blob) is not None,
            "REGISTERED_MIGRATION_SHA_INVALID")
    require(SQL_PATH.fullmatch(path) is not None,
            "REGISTERED_MIGRATION_PATH_INVALID")
    root = repo.resolve()
    target = repo / path
    require(target.is_file() and not target.is_symlink() and
            target.resolve().is_relative_to(root),
            "REGISTERED_MIGRATION_PATH_UNSAFE")
    require(git(repo, "rev-parse", "HEAD") == sha and
            git(repo, "rev-parse", f"{sha}:{path}") == blob,
            "REGISTERED_MIGRATION_SOURCE_CHANGED")
    data = target.read_bytes()
    actual_blob = hashlib.sha1(
        b"blob " + str(len(data)).encode() + b"\0" + data
    ).hexdigest()
    require(actual_blob == blob and 0 < len(data) <= MAX_SQL_BYTES and
            b"\0" not in data and not data.startswith(b"\xef\xbb\xbf"),
            "REGISTERED_MIGRATION_BYTES_UNSAFE")
    try:
        sql = data.decode("utf-8")
    except UnicodeError:
        raise Blocked("REGISTERED_MIGRATION_ENCODING_INVALID") from None
    require(bool(sql.strip()), "REGISTERED_MIGRATION_EMPTY")
    return sql


class Client:
    def __init__(self, ref: str, token: str):
        require(PROJECT.fullmatch(ref) is not None, "REGISTERED_MIGRATION_PROJECT_INVALID")
        require(bool(token), "REGISTERED_MIGRATION_CREDENTIAL_MISSING")
        self.ref = ref
        self._token = token

    def _request(self, path: str, payload: dict[str, str] | None = None) -> Any:
        require(
            (payload is None and path == "")
            or (path == "/database/query/read-only" and payload == {"query": LEDGER})
            or (path == "/database/migrations" and isinstance(payload, dict)
                and set(payload) == {"name", "query"}
                and bool(payload["name"]) and bool(payload["query"])),
            "REGISTERED_MIGRATION_API_DENIED",
        )
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/json",
            "User-Agent": "WONJAE-Owner-Registered-Migration-v1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        method = "GET" if payload is None else "POST"
        try:
            req = Request(f"{ROOT}/{self.ref}{path}", data=body, headers=headers,
                          method=method)
            with urlopen(req, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            operation = {
                "": "PROJECT_READ",
                "/database/query/read-only": "DATABASE_READ",
                "/database/migrations": "MIGRATIONS_WRITE",
            }[path]
            if exc.code in (401, 403):
                raise Blocked(
                    f"REGISTERED_MIGRATION_HTTP_{exc.code}_{operation}"
                ) from None
            raise Blocked(f"REGISTERED_MIGRATION_HTTP_{exc.code}") from None
        except (URLError, OSError, ValueError):
            raise Blocked("REGISTERED_MIGRATION_PROVIDER_UNVERIFIED") from None

    def project(self) -> None:
        data = self._request("")
        require(isinstance(data, dict) and
                (data.get("id") == self.ref or data.get("ref") == self.ref),
                "REGISTERED_MIGRATION_TARGET_UNVERIFIED")

    def ledger(self) -> list[tuple[str, str]]:
        result = self._request("/database/query/read-only", {"query": LEDGER})
        if isinstance(result, dict):
            result = result.get("result")
        require(isinstance(result, list), "REGISTERED_MIGRATION_LEDGER_UNVERIFIED")
        entries = []
        for row in result:
            require(isinstance(row, dict) and
                    isinstance(row.get("name"), str) and
                    isinstance(row.get("version"), str),
                    "REGISTERED_MIGRATION_LEDGER_UNVERIFIED")
            entries.append((row["version"], row["name"]))
        require(len(entries) == len(set(entries)) and
                len({name for _, name in entries}) == len(entries),
                "REGISTERED_MIGRATION_LEDGER_DUPLICATED")
        return entries

    def apply(self, name: str, sql: str) -> None:
        require(MIGRATION.fullmatch(name) is not None,
                "REGISTERED_MIGRATION_NAME_INVALID")
        self._request("/database/migrations", {"name": name, "query": sql})


def execute(
    client: Client, repo: Path, sha: str, path: str, blob: str,
    migration_name: str, prior_name: str,
) -> dict[str, Any]:
    require(MIGRATION.fullmatch(migration_name) is not None and
            MIGRATION.fullmatch(prior_name) is not None and
            migration_name != prior_name, "REGISTERED_MIGRATION_NAME_INVALID")
    sql = source_sql(repo, sha, path, blob)
    client.project()
    before = client.ledger()
    require(bool(before) and before[-1][1] == prior_name and
            migration_name not in [name for _, name in before],
            "REGISTERED_MIGRATION_HISTORY_NOT_READY")
    # No generic SQL fallback and no migration retry for a partial/unverified result.
    client.apply(migration_name, sql)
    after = client.ledger()
    require(len(after) == len(before) + 1 and after[:-1] == before and
            after[-1][1] == migration_name and
            after[-1][0] not in {version for version, _ in before},
            "REGISTERED_MIGRATION_POST_LEDGER_UNVERIFIED")
    return {
        "result": "REGISTERED_SCHEMA_APPLIED_RUNTIME_NOT_VERIFIED",
        "source_sha": sha,
        "project_ref": client.ref,
        "migration_name": migration_name,
        "source_git_blob": blob,
        "sql_sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "previous_migration_count": len(before),
        "migration_version": after[-1][0],
        "runtime_psql": "NOT_RUN",
        "concurrent_sessions": "NOT_RUN",
        "synthetic_cleanup": "NOT_RUN",
        "production": "NOT_RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for field in (
        "repo", "source-sha", "project-ref", "migration-path",
        "migration-blob", "migration-name", "expected-prior-migration",
        "summary", "evidence",
    ):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    try:
        result = execute(
            Client(args.project_ref, os.getenv("REGISTERED_SUPABASE_ACCESS_TOKEN", "")),
            Path(args.repo), args.source_sha, args.migration_path,
            args.migration_blob, args.migration_name, args.expected_prior_migration,
        )
        evidence = Path(args.evidence)
        evidence.write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        evidence.chmod(0o600)
        with Path(args.summary).open("a", encoding="utf-8") as summary:
            summary.write(
                "\n## Registered Development migration\n"
                f"- migration: {result['migration_name']}\n"
                f"- applied version: {result['migration_version']}\n"
                "- exact blob: VERIFIED\n- existing history: PRESERVED\n"
                "- runtime / concurrency / cleanup: NOT_RUN\n"
                "- Production / device: NOT_RUN\n"
            )
        return 0
    except Blocked as exc:
        print(f"::error::{exc}")
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print("::error::REGISTERED_MIGRATION_RESULT_UNVERIFIED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
