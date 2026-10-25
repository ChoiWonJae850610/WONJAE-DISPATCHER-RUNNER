#!/usr/bin/env python3
"""Owner-only, create-only WAFL Preview Vercel env bridge; no secret output.

This is dormant until exact common/product registration and Owner activation.
Never rerun automatically after an uncertain create outcome.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

REPOSITORY = "ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER"
PROJECT_ID = "prj_vJ4cfeJtdoDBSm9QWEo9mvPT9xK4"
PROJECT_NAME = "project-74o2z"
TEAM_ID = "team_v4qmE9VlvZnnT0gpesukVJSj"
BRANCH = "cloud-dev-v1"
BASE = "https://api.vercel.com"
SOURCE_KEYS = (
    ("WAFL_R2_PREVIEW_WORKER_URL", "R2_WORKER_UPLOAD_URL", "encrypted"),
    ("WAFL_R2_WORKER_UPLOAD_SECRET", "R2_WORKER_UPLOAD_SECRET", "sensitive"),
    ("WAFL_PDF_DEV_WORKER_URL", "WAFLOW_PDF_GENERATOR_URL", "encrypted"),
    ("WAFL_PDF_GENERATOR_TOKEN", "WAFLOW_PDF_GENERATOR_TOKEN", "sensitive"),
)
TARGET_KEYS = frozenset(value for _, value, _ in SOURCE_KEYS)


class BridgeBlocked(RuntimeError):
    pass


def require(ok: bool, code: str) -> None:
    if not ok:
        raise BridgeBlocked(code)


def trusted_execution(env: dict[str, str]) -> None:
    require(
        env.get("GITHUB_REPOSITORY") == REPOSITORY
        and env.get("GITHUB_REF") == "refs/heads/main"
        and env.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
        "WAFL_VERCEL_TRUSTED_OWNER_WORKFLOW_REQUIRED",
    )


def valid_worker_origin(value: str, worker: str) -> bool:
    try:
        url = urllib.parse.urlsplit(value)
        host = url.hostname or ""
        return (
            url.scheme == "https" and url.netloc == host
            and host.startswith(worker + ".")
            and host.endswith(".workers.dev")
            and re.fullmatch(r"[a-z0-9.-]{10,160}", host) is not None
            and url.path in ("", "/") and not url.query and not url.fragment
            and url.username is None and url.password is None
        )
    except ValueError:
        return False


def required_values(env: dict[str, str]) -> tuple[str, list[dict[str, object]]]:
    token = env.get("WAFL_VERCEL_TOKEN", "").strip()
    require(bool(token) and "\n" not in token and "\r" not in token,
            "WAFL_VERCEL_OWNER_API_CREDENTIAL_MISSING")
    values = {}
    for source, target, _kind in SOURCE_KEYS:
        val = env.get(source, "")
        require(0 < len(val) <= 4096 and val == val.strip()
                and "\n" not in val and "\r" not in val,
                "WAFL_VERCEL_PROTECTED_SOURCE_INPUT_MISSING")
        values[target] = val
    require(valid_worker_origin(values["R2_WORKER_UPLOAD_URL"], "wafl-r2-dev"),
            "WAFL_VERCEL_R2_ORIGIN_NOT_VERIFIED")
    require(valid_worker_origin(values["WAFLOW_PDF_GENERATOR_URL"], "wafl-pdf-dev"),
            "WAFL_VERCEL_PDF_ORIGIN_NOT_VERIFIED")
    require(len(values["R2_WORKER_UPLOAD_SECRET"]) >= 32
            and len(values["WAFLOW_PDF_GENERATOR_TOKEN"]) >= 32
            and values["R2_WORKER_UPLOAD_SECRET"] != values["WAFLOW_PDF_GENERATOR_TOKEN"],
            "WAFL_VERCEL_SIGNING_TOKEN_IDENTITY_INVALID")
    body = [
        {"key": dest, "value": values[dest], "type": kind,
         "target": ["preview"], "gitBranch": BRANCH}
        for _src, dest, kind in SOURCE_KEYS
    ]
    return token, body


def http_json(method: str, route: str, token: str,
              body: list[dict[str, object]] | None = None) -> object:
    require(method in ("GET", "POST"), "WAFL_VERCEL_UNSUPPORTED_HTTP_METHOD")
    require(route.startswith("/v9/projects/") or route.startswith("/v10/projects/"),
            "WAFL_VERCEL_UNREVIEWED_API_ROUTE")
    require("\r" not in route and "\n" not in route,
            "WAFL_VERCEL_UNREVIEWED_API_ROUTE")
    request = urllib.request.Request(
        BASE + route,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "Content-Type": "application/json"},
        method=method,
    )
    # Do not print response, request, exception or Vercel token. No redirect adoption.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=12) as response:
            data = response.read(1_000_001)
            require(len(data) <= 1_000_000,
                    "WAFL_VERCEL_API_RESPONSE_UNBOUNDED")
            return json.loads(data)
    except BridgeBlocked:
        raise
    except Exception:
        raise BridgeBlocked("WAFL_VERCEL_API_RESPONSE_UNVERIFIED") from None


def route(suffix: str, *, write: bool = False) -> str:
    version = "v10" if write else "v9"
    return (
        f"/{version}/projects/{PROJECT_ID}{suffix}"
        f"?teamId={TEAM_ID}"
        + ("&upsert=false" if write else "")
        + ("&decrypt=false" if suffix == "/env" and not write else "")
    )


def check_project(value: object) -> None:
    require(isinstance(value, dict)
            and value.get("id") == PROJECT_ID
            and value.get("name") == PROJECT_NAME
            and value.get("accountId") == TEAM_ID,
            "WAFL_VERCEL_EXACT_TARGET_UNVERIFIED")


def env_inventory(value: object) -> list[dict[str, object]]:
    require(isinstance(value, dict) and isinstance(value.get("envs"), list),
            "WAFL_VERCEL_ENV_INVENTORY_UNVERIFIED")
    pagination = value.get("pagination")
    require(pagination is None or
            (isinstance(pagination, dict) and not pagination.get("next")),
            "WAFL_VERCEL_ENV_INVENTORY_PAGINATED")
    require(value.get("hiddenProductionEnvCount", 0) == 0,
            "WAFL_VERCEL_HIDDEN_PRODUCTION_ENV_UNVERIFIED")
    items = value["envs"]
    require(len(items) <= 1000 and all(isinstance(item, dict) for item in items),
            "WAFL_VERCEL_ENV_INVENTORY_UNBOUNDED")
    return items


def before_create(items: list[dict[str, object]]) -> None:
    # Never override or collide with Production, inherited Preview or other branches.
    require(not any(item.get("key") in TARGET_KEYS for item in items),
            "WAFL_VERCEL_TARGET_ENV_NAME_ALREADY_EXISTS")


def after_create(items: list[dict[str, object]]) -> None:
    for _, key, kind in SOURCE_KEYS:
        matches = [item for item in items if item.get("key") == key]
        require(len(matches) == 1 and matches[0].get("type") == kind
                and matches[0].get("target") == ["preview"]
                and matches[0].get("gitBranch") == BRANCH,
                "WAFL_VERCEL_PREVIEW_METADATA_READBACK_UNVERIFIED")


def bridge(env: dict[str, str], *, execute: bool) -> str:
    trusted_execution(env)
    token, body = required_values(env)
    check_project(http_json("GET", route(""), token))
    before_create(env_inventory(http_json("GET", route("/env"), token)))
    if not execute:
        return "WAFL_VERCEL_PREVIEW_READ_ONLY_ELIGIBLE"
    # One create-only batch. An ambiguous response is a terminal partial-state guard,
    # never an authorization to retry or delete/overwrite the other names.
    http_json("POST", route("/env", write=True), token, body)
    after_create(env_inventory(http_json("GET", route("/env"), token)))
    return "WAFL_VERCEL_PREVIEW_FOUR_ENV_METADATA_PASS"


def main() -> int:
    try:
        mode = sys.argv[1:] if len(sys.argv) > 1 else []
        require(mode in (["--inspect"], ["--apply"]),
                "WAFL_VERCEL_OWNER_MODE_INVALID")
        print(bridge(dict(os.environ), execute=mode == ["--apply"]))
        return 0
    except BridgeBlocked as error:
        print("::error::" + str(error))
        return 1
    except Exception:
        print("::error::WAFL_VERCEL_UNKNOWN_FAIL_CLOSED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
