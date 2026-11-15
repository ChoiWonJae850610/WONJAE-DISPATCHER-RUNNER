"""Thin mutation-boundary adapter over the existing cross-route SharedFence.

Existing handlers still own authorization, path limits and Provider/QA gates.
No daemon, queue, credential store or new approval mechanism is introduced.
"""
from __future__ import annotations

import base64
import contextvars
import functools
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

from .registered_source_fence import GitHubEvidenceReader, registered_configuration
from .source_writer_fence import FenceError, SharedFence, atomic_json, sha40

_CURRENT = contextvars.ContextVar("trusted_product_mutation", default=None)


class MutationFence:
    def __init__(self, registry: Path, project: str, actor: str, role: str,
                 base: str, approved=(), *, persist=False):
        self.registry, self.project, self.actor, self.role = registry, project, actor, role
        self.base, self.approved, self.persist = base, list(approved), persist
        self.fence = self.ticket = self.configuration = None
        self.initialized = False
        self.session_file = None

    def _initialize(self, *, require_session=False):
        if self.initialized:
            return
        config = registered_configuration(self.registry, self.project)
        self.initialized = True
        if config is None:
            return  # Current legacy route, no opt-in/credential/backend access.
        if self.actor == "legacy_source":
            raise FenceError("REGISTERED_LEGACY_SOURCE_ROUTE_RETIRED")
        if not sha40(self.base) or not re.fullmatch(r"[a-z_]{1,32}", self.actor):
            raise FenceError("EXACT_MUTATION_SCOPE_REQUIRED")
        if self.role not in {"document_merge", "source_recovery", "reconciliation",
                             "provider_state_update", "direct_worker"}:
            raise FenceError("REGISTERED_MUTATION_ROLE_REQUIRED")

        def read(endpoint):
            env = os.environ.copy()
            token = (env.get("RUNNER_READ_TOKEN") or env.get("RUNNER_TOKEN")
                     or env.get("CONTROL_READ_TOKEN")
                     if endpoint.startswith("repos/" + config["runner_repository"] + "/")
                     else env.get("PRODUCT_TOKEN") or env.get("PRODUCT_GH_TOKEN")
                     or env.get("PRODUCT_WRITE_TOKEN")
                     or env.get("OWNER_GITHUB_TOKEN") or env.get("GH_TOKEN"))
            if not token:
                raise FenceError("EXISTING_MUTATION_READ_CREDENTIAL_UNAVAILABLE")
            result = subprocess.run(["gh", "api", "--method", "GET", endpoint],
                                    env={**env, "GH_TOKEN": token}, check=True,
                                    capture_output=True, text=True, timeout=20)
            return json.loads(result.stdout)

        policy = subprocess.run(["git", "-C", str(self.registry.parent), "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True,
                                timeout=10).stdout.strip()
        own = {"repository": os.environ["GITHUB_REPOSITORY"],
               "run_id": int(os.environ["GITHUB_RUN_ID"]),
               "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
        jobs = read(f"repos/{own['repository']}/actions/runs/{own['run_id']}/jobs"
                    "?filter=latest&per_page=100")
        names = config.get("writer_jobs", {}).get(self.actor, {}).get(self.project, [])
        matches = [j for j in jobs.get("jobs", []) if j.get("name") in names
                   and j.get("status") == "in_progress"
                   and j.get("runner_name") == os.environ.get("RUNNER_NAME")
                   and j.get("run_attempt") == own["attempt"]]
        if len(matches) != 1 or jobs.get("total_count") != len(jobs.get("jobs", [])):
            raise FenceError("EXACT_MUTATION_JOB_IDENTITY_UNVERIFIED")
        own["job_id"] = matches[0]["id"]
        binding = {"project": self.project, "repository": config["repository"],
                   "branch": config["branch"], "remote_sha": self.base,
                   "common_policy_sha": policy, "product_policy_sha": self.base,
                   "backend_id": config["backend_id"], "run": own,
                   "participant_revisions": config["participant_revisions"],
                   "readonly_workflow_blobs": config.get("readonly_workflow_blobs", [])}
        parent = Path(os.environ["RUNNER_TEMP"])
        self.session_file = parent / (
            f"product-fence-{own['run_id']}-{own['attempt']}-{self.project}-{self.actor}.json"
        )
        if require_session and not self.session_file.is_file():
            raise FenceError("EXACT_EXISTING_MUTATION_SESSION_REQUIRED")
        self.configuration = dict(config, product_gate="BRANCH_STATE_ONLY")
        if self.role == "direct_worker":
            import yaml
            payload = read(f"repos/{config['repository']}/contents/{config['state_path']}"
                           f"?ref={self.base}")
            state = yaml.safe_load(base64.b64decode(payload["content"]).decode("utf-8"))
            self.configuration["product_gate"] = state["next_action"]["type"]
        self._read = read
        self.fence = SharedFence(Path(config["root"]), binding, GitHubEvidenceReader(
            binding, self.configuration, read,
        ))
        if self.fence.scope != "registered":
            raise FenceError("FIXTURE_CANNOT_AUTHORIZE_PRODUCT_MUTATION")
        if self.session_file.exists():
            self.ticket = json.loads(self.session_file.read_text())
            if self.ticket.get("entrypoint") != self.actor or self.ticket.get("released"):
                raise FenceError("MUTATION_SESSION_ENTRYPOINT_CHANGED")
            self.configuration["approved_prs"] = self.ticket.get("approved_prs", [])
            self.configuration["owned_pr"] = self.ticket.get("owned_pr")
        else:
            task = f"{self.project}-GIT-{own['run_id']}-{own['attempt']}-{self.actor}"
            approved = [{"number": p[0], "head": p[1], "base": config["branch"],
                         "task_id": task} for p in self.approved]
            binding["approved_prs"] = approved
            self.fence.binding["approved_prs"] = approved
            self.configuration["approved_prs"] = approved
            self.ticket = self.fence.acquire({
                "task_id": task, "route": self.role, "request_id": secrets.token_hex(16),
                "expires_at": time.time() + 900, "lease_seconds": 4500,
            })
            self.ticket["entrypoint"] = self.actor
            atomic_json(self.session_file, self.ticket)

    def run(self, phase, callback):
        self._initialize()
        if self.fence is None:
            return callback()
        try:
            with self.fence.guarded(self.ticket, phase):
                return callback()
        except BaseException:
            self.abort()
            raise

    def point(self, phase):
        return self.run(phase, lambda: None)

    def observed_pr(self, number, head=None):
        if self.fence is None:
            return
        raw = self._read(f"repos/{self.fence.binding['repository']}/pulls/{int(number)}")
        if (raw["base"]["ref"] != self.fence.binding["branch"]
                or (head and raw["head"]["sha"] != head)):
            raise FenceError("EXACT_MUTATION_PR_RECEIPT_REQUIRED")
        pr = {"number": raw["number"], "base": raw["base"]["ref"],
              "head": raw["head"]["sha"], "task_id": self.ticket["task_id"]}
        self.fence.approve_observed_pr(self.ticket, pr)
        self.ticket["approved_prs"] = [p for p in self.ticket.get("approved_prs", [])
                                        if p["number"] != pr["number"]] + [pr]
        self.ticket["owned_pr"] = pr
        self.configuration.update(approved_prs=self.ticket["approved_prs"], owned_pr=pr)
        atomic_json(self.session_file, self.ticket)

    def observed_merge(self, number, integrated):
        if self.fence is None:
            return
        self.observed_pr(number)
        pr = self._read(f"repos/{self.fence.binding['repository']}/pulls/{int(number)}")
        if pr.get("merged") is not True or pr.get("merge_commit_sha") != integrated:
            raise FenceError("EXACT_MUTATION_MERGE_RECEIPT_REQUIRED")
        self.fence.acknowledge_integration(self.ticket, {
            "repository": self.fence.binding["repository"], "number": pr["number"],
            "head": pr["head"]["sha"], "base": pr["base"]["ref"],
            "merged": True, "integrated_sha": integrated,
        })

    def finish(self):
        if self.fence is not None:
            self.fence.release(self.ticket, {
                "task_id": self.ticket["task_id"], "epoch": self.ticket["epoch"],
                "child_terminal": True, "publication_reconciled": True,
            })
            # Keep the exact receipt for later audit; a released session is not reusable.
            self.ticket["released"] = True
            atomic_json(self.session_file, self.ticket)

    def abort(self):
        if self.fence is not None and self.ticket:
            try:
                self.fence.uncertain(self.ticket)
            except (FenceError, OSError):
                pass  # Preserve the already ambiguous transaction/grant.


def writer_entry(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if _CURRENT.get() is not None:
            return function(*args, **kwargs)
        slot = {"scope": None}
        token = _CURRENT.set(slot)
        try:
            result = function(*args, **kwargs)
            scope = slot["scope"]
            if scope and not scope.persist:
                scope.finish()
            return result
        except BaseException:
            if slot["scope"]:
                slot["scope"].abort()
            raise
        finally:
            _CURRENT.reset(token)
    return wrapped


def bind_writer(registry, project, actor, role, base, approved=(), *, persist=False):
    slot = _CURRENT.get()
    if slot is None:
        raise FenceError("MISSING_TRUSTED_WRITER_ENTRY")
    if slot["scope"]:
        prior = slot["scope"]
        nested_stage = prior.actor == "lean_ci_publish" and actor == "lean_ci_stage"
        if (prior.project != project or prior.base != base or prior.role != role
                or (prior.actor != actor and not nested_stage)):
            raise FenceError("NESTED_MUTATION_SCOPE_CHANGED")
        return prior
    scope = MutationFence(Path(registry), project, actor, role, base, approved, persist=persist)
    slot["scope"] = scope
    return scope


def current_scope():
    slot = _CURRENT.get()
    return slot["scope"] if slot else None


def guard_point(phase):
    scope = current_scope()
    if _CURRENT.get() is not None and scope is None:
        raise FenceError("WRITER_CONTEXT_NOT_BOUND")
    if scope:
        scope.point(phase)


def guarded_command(arguments, callback):
    commands = {"commit": "before_commit", "push": "before_push", "add": "before_state",
                "apply": "before_state", "update-ref": "before_push"}
    phase = next((commands[x] for x in arguments if x in commands), None)
    scope = current_scope()
    if phase and _CURRENT.get() is not None and scope is None:
        raise FenceError("WRITER_CONTEXT_NOT_BOUND")
    if scope and phase:
        return scope.run(phase, callback)
    return callback()


def guarded_api(method, endpoint, payload, callback):
    scope = current_scope()
    if method.upper() != "GET" and _CURRENT.get() is not None and scope is None:
        raise FenceError("WRITER_CONTEXT_NOT_BOUND")
    if method.upper() == "GET" or scope is None:
        return callback()
    phase = publication_phase(endpoint)
    result = scope.run(phase, callback)
    if isinstance(result, dict):
        if endpoint.endswith("/pulls") and "number" in result:
            scope.observed_pr(result["number"], result.get("head", {}).get("sha"))
        elif endpoint.endswith("/merge") and result.get("merged"):
            scope.observed_merge(int(endpoint.split("/")[-2]), result["sha"])
    return result


def publication_phase(endpoint):
    if endpoint.endswith("/merge"):
        return "before_merge"
    if "/git/refs" in endpoint:
        return "before_push"
    if endpoint.endswith("/git/commits"):
        return "before_commit"
    if "/contents/" in endpoint or endpoint.endswith(("/git/blobs", "/git/trees")):
        return "before_state"
    return "before_pr"


def api_arguments(arguments, callback):
    method, endpoint = "GET", None
    for i, value in enumerate(arguments):
        if value in {"--method", "-X"}:
            method = arguments[i + 1]
        if value.startswith("repos/"):
            endpoint = value
    return guarded_api(method, endpoint or "", None, callback)


class FencedApi:
    def __init__(self, delegate):
        self.delegate = delegate

    def get(self, *args, **kwargs):
        return self.delegate.get(*args, **kwargs)

    def post(self, endpoint, payload):
        return guarded_api("POST", endpoint, payload,
                           lambda: self.delegate.post(endpoint, payload))

    def patch(self, endpoint, payload):
        return guarded_api("PATCH", endpoint, payload,
                           lambda: self.delegate.patch(endpoint, payload))
