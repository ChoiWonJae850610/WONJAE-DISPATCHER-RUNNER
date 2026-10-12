#!/usr/bin/env python3
"""CLASSMO 016: strictly read-only Development preflight; no migration execution."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = "https://api.supabase.com/v1/projects/"
MIGRATION = "supabase/migrations/20261012001600_016_editorial_drafts.sql"
MANIFEST = "supabase/migrations/SHA256SUMS"
LEDGER = "select version::text as version, name::text as name from supabase_migrations.schema_migrations order by version;"
ENV = "select count(*) filter (where environment in ('development','preview'))::int as allowed, count(*) filter (where environment not in ('development','preview'))::int as forbidden from public.classmo_environment;"
DRAFT = "select c.relname::text as name from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relname='class_page_drafts' and c.relkind='r';"
ALLOWED_SQL = frozenset((LEDGER, ENV, DRAFT))
NAME = re.compile(r"^[0-9]{14}_([0-9]{3}_[a-z0-9_]+)\.sql$")

class Blocked(Exception):
    pass

def require(ok, code):
    if not ok:
        raise Blocked(code)

def verify(repo, source_sha, blob):
    require(re.fullmatch(r"[a-f0-9]{40}", source_sha) is not None and
            re.fullmatch(r"[a-f0-9]{40}", blob) is not None, "C016_IDENTITY_INVALID")
    try:
        actual = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                check=True, text=True, capture_output=True, timeout=15).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise Blocked("C016_GIT_UNVERIFIED") from None
    require(actual == source_sha, "C016_SOURCE_SHA_CHANGED")
    def read(relative):
        path = repo / relative
        require(path.is_file() and not path.is_symlink() and
                path.resolve().is_relative_to(repo.resolve()), "C016_SOURCE_PATH_INVALID")
        return path.read_bytes()
    data = read(MIGRATION)
    require(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\x00" + data).hexdigest() == blob,
            "C016_MIGRATION_BLOB_CHANGED")
    lines = read(MANIFEST).decode().splitlines()
    require(len(lines) == 16, "C016_MANIFEST_SIZE_INVALID")
    names = []
    for line in lines:
        parts = line.split("  ")
        require(len(parts) == 2 and re.fullmatch(r"[a-f0-9]{64}", parts[0]) is not None,
                "C016_MANIFEST_FORMAT_INVALID")
        m = NAME.fullmatch(parts[1])
        require(m is not None, "C016_MIGRATION_NAME_INVALID")
        names.append(m.group(1))
        require(hashlib.sha256(read("supabase/migrations/" + parts[1])).hexdigest() == parts[0],
                "C016_MIGRATION_HASH_INVALID")
    require(len(set(names)) == 16 and names[-2:] == ["015_course_configuration_ambiguity", "016_editorial_drafts"],
            "C016_MIGRATION_SEQUENCE_INVALID")
    return names[:15], hashlib.sha256(data).hexdigest()

class Client:
    def __init__(self, ref, token):
        require(re.fullmatch(r"[a-z]{20}", ref) is not None, "C016_PROJECT_REF_INVALID")
        require(bool(token), "C016_CREDENTIAL_MISSING")
        self.ref, self.token = ref, token

    def request(self, path, query=None):
        require((path == "" and query is None) or
                (path == "/database/query/read-only" and query in ALLOWED_SQL),
                "C016_MUTATING_OR_UNREVIEWED_ENDPOINT")
        body = None if query is None else json.dumps({"query": query}).encode()
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json",
                   "User-Agent": "CLASSMO-016-readonly"}
        if body:
            headers["Content-Type"] = "application/json"
        try:
            req = Request(ROOT + self.ref + path, data=body, headers=headers,
                          method="GET" if body is None else "POST")
            with urlopen(req, timeout=45) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            raise Blocked("C016_PROVIDER_HTTP_" + str(exc.code)) from None
        except (URLError, OSError, ValueError):
            raise Blocked("C016_PROVIDER_UNVERIFIED") from None

    def read(self, sql):
        require(sql in ALLOWED_SQL, "C016_SQL_NOT_ALLOWED")
        rows = self.request("/database/query/read-only", sql)
        if isinstance(rows, dict):
            rows = rows.get("result")
        require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
                "C016_QUERY_SHAPE_UNVERIFIED")
        return rows

def execute(client, repo, source_sha, blob):
    prior_names, sha256 = verify(repo, source_sha, blob)
    project = client.request("")
    require(isinstance(project, dict) and project.get("id") == client.ref,
            "C016_PROJECT_NOT_VERIFIED")
    environments = client.read(ENV)
    require(len(environments) == 1, "C016_ENV_NOT_VERIFIED")
    try:
        valid = int(environments[0]["allowed"]) >= 1 and int(environments[0]["forbidden"]) == 0
    except (KeyError, TypeError, ValueError):
        valid = False
    require(valid, "C016_NONPRODUCTION_GUARD_FAILED")
    names = [row.get("name") for row in client.read(LEDGER)]
    require(len(names) >= 15 and names[-15:] == prior_names and
            len(names) == len(set(names)), "C016_LEDGER_NOT_READY")
    require(client.read(DRAFT) == [], "C016_ALREADY_APPLIED_OR_SCHEMA_DRIFT")
    return {"result": "C016_READONLY_PASS_NOT_APPLIED", "project": "CLASSMO",
            "project_ref": client.ref, "source_sha": source_sha,
            "migration_blob": blob, "migration_sha256": sha256,
            "ledger_001_015": "MATCHED", "migration_016": "NOT_APPLIED",
            "runtime": "NOT_RUN", "concurrency": "NOT_RUN",
            "production": "NOT_RUN", "ota_device": "NOT_RUN"}

def main():
    p = argparse.ArgumentParser()
    for value in ("repo", "source-sha", "project-ref", "migration-blob", "summary", "evidence"):
        p.add_argument("--" + value, required=True)
    a = p.parse_args()
    try:
        info = execute(Client(a.project_ref, os.getenv("CLASSMO_SUPABASE_ACCESS_TOKEN", "")),
                       Path(a.repo).resolve(), a.source_sha, a.migration_blob)
    except Blocked as exc:
        print("CLASSMO_016_PREFLIGHT_BLOCKED code=" + str(exc), flush=True)
        return 1
    Path(a.evidence).write_text(json.dumps(info, sort_keys=True, indent=2) + "\n")
    with Path(a.summary).open("a") as out:
        out.write("## CLASSMO 016 read-only preflight\n\n")
        for k, v in info.items():
            out.write("- " + k + ": " + str(v) + "\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
