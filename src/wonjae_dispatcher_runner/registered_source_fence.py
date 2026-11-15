"""Opt-in trusted-parent adapter. Existing routes are unchanged without registration.

Reads happen only at work/publication boundaries, never in the Drive polling loop.
Runtime topology is private configuration pinned by the canonical registry hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .source_writer_fence import ACTIVE, CONTRACT, FenceError, SharedFence, atomic_json, sha40


class GitHubEvidenceReader:
    def __init__(self, binding: dict, configuration: dict, read):
        self.binding, self.configuration, self.read = binding, configuration, read

    def collection(self, endpoint: str, key: str | None = None) -> list:
        answer = []
        for page in range(1, 21):
            data = self.read(endpoint + ("&" if "?" in endpoint else "?")
                             + f"per_page=100&page={page}")
            items = data.get(key) if key else data
            if not isinstance(items, list):
                raise FenceError("GITHUB_INVENTORY_INVALID")
            answer.extend(items)
            if len(items) < 100:
                if key and isinstance(data.get("total_count"), int):
                    if len(answer) < data["total_count"]:
                        raise FenceError("GITHUB_INVENTORY_INCOMPLETE")
                return answer
        raise FenceError("GITHUB_INVENTORY_PAGE_LIMIT")

    def __call__(self) -> dict:
        b, c = self.binding, self.configuration
        observed = time.time()
        branch = self.read(f"repos/{b['repository']}/branches/{b['branch']}")
        remote_sha = branch.get("commit", {}).get("sha")
        policy_sha = self.read(
            f"repos/{c['common_repository']}/branches/main",
        ).get("commit", {}).get("sha")
        deployed_runner = self.read(
            f"repos/{c['runner_repository']}/branches/main",
        ).get("commit", {}).get("sha")
        if deployed_runner != b["participant_revisions"]["direct_worker"]:
            raise FenceError("TRUSTED_RUNNER_REVISION_NOT_DEPLOYED")
        if any(revision != deployed_runner for role, revision in
               b["participant_revisions"].items() if role != "watch"):
            raise FenceError("TRUSTED_BRANCH_WRITER_REVISION_NOT_DEPLOYED")
        prs = self.collection(f"repos/{b['repository']}/pulls?state=open&base={b['branch']}")
        runs, jobs = [], []
        for repository in (b["repository"], c["runner_repository"]):
            for status in sorted(ACTIVE):
                for run in self.collection(
                    f"repos/{repository}/actions/runs?status={status}", "workflow_runs",
                ):
                    raw_jobs = self.collection(
                        f"repos/{repository}/actions/runs/{run['id']}/jobs?filter=latest", "jobs",
                    )
                    rule = next((rule for rule in c["workflow_rules"] if
                                 rule["repository"] == repository
                                 and rule["path"] == run.get("path")
                                 and rule["revision"] == run.get("head_sha")), None)
                    # Exact trusted workflow/job identities, never run display-title.
                    active_jobs = [j for j in raw_jobs if j.get("status") in ACTIVE]
                    scopes = {rule.get("jobs", {}).get(j.get("name")) for j in active_jobs
                              } if rule else {None}
                    scope = next(iter(scopes)) if len(scopes) == 1 else None
                    identity = {"repository": repository, "run_id": run["id"],
                                "attempt": run.get("run_attempt")}
                    own = b.get("run")
                    is_own = bool(own and len(active_jobs) == 1
                                  and all(identity[k] == own.get(k) for k in identity)
                                  and any(j["id"] == own.get("job_id") for j in active_jobs))
                    item = {"status": run["status"], "project": scope,
                            "scope_attested": bool(rule and scope and active_jobs),
                            "run": own if is_own else identity,
                            "fenced_entry": bool(rule and is_own and rule.get("fenced")),
                            "read_only": bool(rule and rule.get("read_only")),
                            "workflow_blob": rule.get("blob") if rule else None}
                    runs.append(item)
                    for job in active_jobs:
                        jobs.append({**item, "status": job["status"],
                                     "fenced_entry": bool(item["fenced_entry"]
                                                           and job["id"] == own["job_id"])})
        approved = c.get("approved_prs", []) + ([c["owned_pr"]] if c.get("owned_pr") else [])
        normalized = []
        for pr in prs:
            item = {"number": pr["number"], "base": pr["base"]["ref"],
                    "head": pr["head"]["sha"], "task_id": None}
            for own_pr in approved:
                if all(item[k] == own_pr.get(k) for k in ("number", "base", "head")):
                    item["task_id"] = own_pr["task_id"]
            normalized.append(item)
        return {**b, "observed_at": observed, "remote_sha": remote_sha,
                "common_policy_sha": policy_sha,
                "product_gate": c["product_gate"], "runs_complete": True,
                "jobs_complete": True, "prs_complete": True,
                "runner_reconciled": c.get("all_participants_installed") is True,
                "participants": c["participants"], "runs": runs, "jobs": jobs,
                "open_prs": normalized}


def registered_configuration(registry: Path, project: str, environment=None):
    import yaml
    environment = os.environ if environment is None else environment
    product = yaml.safe_load(registry.read_text())["projects"][project]
    execution = product.get("execution", {})
    registration = execution.get("source_writer_fence")
    if registration is None:
        return None
    if (registration.get("contract") != CONTRACT or registration.get("required") is not True
            or execution.get("mode") not in {"direct_worker", "personal_desktop_local"}):
        raise FenceError("CANONICAL_FENCE_REGISTRATION_INVALID")
    name = environment.get("SOURCE_WRITER_FENCE_CONFIG")
    if not name:
        raise FenceError("REGISTERED_PRIVATE_CONFIGURATION_MISSING")
    file = Path(name)
    if not file.is_absolute() or not file.is_file() or file.is_symlink():
        raise FenceError("PRIVATE_CONFIGURATION_PATH_INVALID")
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != registration.get("configuration_sha256"):
        raise FenceError("PRIVATE_CONFIGURATION_HASH_CHANGED")
    config = json.loads(data)
    if isinstance(config.get("projects"), dict):
        config = {**config, **config["projects"].get(project, {})}
    if config.get("contract") != CONTRACT or config.get("all_participants_installed") is not True:
        raise FenceError("ALL_MUTATING_PARTICIPANTS_NOT_INSTALLED")
    if config.get("evidence_scope") != "registered":
        raise FenceError("FIXTURE_CONFIGURATION_CANNOT_ENABLE_ROUTE")
    from .writer_participation import validate_installed_code
    validate_installed_code(config)
    if (config.get("repository") != product["repository"]
            or config.get("branch") != product["branch"]):
        raise FenceError("REGISTERED_PRODUCT_BINDING_CHANGED")
    if "roots" in config:
        config["root"] = config["roots"]["windows" if os.name == "nt" else "linux"]
    return config


class ExistingGitCredentialReader:
    """Same noninteractive Git credential helper as the existing Windows Watch.

    Prepared only; construction does not read credentials. No enrollment, key
    copying, persistence or GitHub mutation. Tokens stay inside the trusted call.
    """
    def __init__(self):
        self._token = None

    def __call__(self, endpoint: str):
        if not endpoint.startswith("repos/") or any(x in endpoint for x in ("..", ":", "\\")):
            raise FenceError("GITHUB_READ_ENDPOINT_REJECTED")
        if "/issues" in endpoint:
            raise FenceError("FENCE_MUST_NOT_READ_ISSUE_AGAIN")
        if self._token is None:
            env = {**os.environ, "GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"}
            result = subprocess.run(
                ["git", "credential", "fill"],
                input="protocol=https\nhost=github.com\n\n", env=env,
                capture_output=True, text=True, timeout=10, check=False,
            )
            fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if result.returncode or not fields.get("password"):
                raise FenceError("EXISTING_GIT_READ_CREDENTIAL_UNAVAILABLE")
            self._token = fields["password"]

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, message, headers, newurl):
                raise FenceError("GITHUB_REDIRECT_REJECTED")

        request = urllib.request.Request(
            "https://api.github.com/" + endpoint,
            headers={"Authorization": "Bearer " + self._token,
                     "Accept": "application/vnd.github+json", "User-Agent": "source-writer-fence"},
            method="GET",
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as result:
                return json.load(result)
        except (urllib.error.URLError, ValueError) as exc:
            raise FenceError("GITHUB_READ_UNAVAILABLE") from exc


def watch_adapter(registry: Path, project: str, checkout: Path, configuration: str,
                  ticket: dict | None = None):
    import yaml
    config = registered_configuration(
        registry, project, {"SOURCE_WRITER_FENCE_CONFIG": configuration},
    )
    if config is None:
        raise FenceError("WATCH_FENCE_NOT_CANONICALLY_REGISTERED")
    execution = yaml.safe_load(registry.read_text())["projects"][project]["execution"]
    trial = execution.get("watch_trial", {})
    connected = execution.get("mode") == "personal_desktop_local" and execution.get(
        "watch_status"
    ) == "connected"
    if not connected and not (trial.get("authorized") is True and trial.get("source_only") is True
                             and trial.get("direct_worker_preserved") is True
                             and trial.get("writer_contract") == "shared_project_controller_v1"
                             and isinstance(trial.get("handoff_ref"), str)
                             and bool(trial["handoff_ref"])):
        raise FenceError("WATCH_SOURCE_TRIAL_NOT_REGISTERED")
    if checkout != Path(config["source_root"]):
        raise FenceError("WATCH_SOURCE_ROOT_BINDING_CHANGED")
    def git(directory, *args):
        return subprocess.run(["git", "-C", str(directory), *args], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    common = git(registry.parent, "rev-parse", "HEAD")
    head = git(checkout, "rev-parse", "HEAD")
    source = ticket["remote_sha"] if ticket else head
    binding = {"project": project, "repository": config["repository"],
               "branch": config["branch"], "remote_sha": source,
               "common_policy_sha": common, "product_policy_sha": source,
               "backend_id": config["backend_id"],
               "participant_revisions": config["participant_revisions"],
               "readonly_workflow_blobs": config.get("readonly_workflow_blobs", [])}
    state = yaml.safe_load(git(checkout, "show", f"{source}:{config['state_path']}"))
    config = {**config, "product_gate": state["next_action"]["type"],
              "owned_pr": ticket.get("owned_pr") if ticket else None}
    fence = SharedFence(Path(config["root"]), binding, GitHubEvidenceReader(
        binding, config, ExistingGitCredentialReader(),
    ))
    if fence.scope != "registered":
        raise FenceError("FIXTURE_BACKEND_CANNOT_ENABLE_ROUTE")
    return fence, head


class DirectWorkerFence:
    """Trusted parent facade used by workflow and each actual source-turn guard."""
    def __init__(self, fence: SharedFence, ticket_file: Path):
        self.fence, self.ticket_file = fence, ticket_file

    def acquire(self, request: dict):
        ticket = self.fence.acquire(request)
        atomic_json(self.ticket_file, ticket)
        return ticket

    def ticket(self):
        if not self.ticket_file.is_file() or self.ticket_file.is_symlink():
            raise FenceError("EXACT_PERSISTED_JOB_TICKET_REQUIRED")
        return json.loads(self.ticket_file.read_text())

    def before(self, phase: str):
        return self.fence.verify(self.ticket(), phase)

    def run_source(self, callback):
        with self.fence.guarded(self.ticket(), "before_source"):
            return callback()

    def publish(self, phase: str, callback):
        if phase not in {"before_commit", "before_push", "before_pr", "before_merge"}:
            raise FenceError("PUBLICATION_PHASE_REQUIRED")
        with self.fence.guarded(self.ticket(), phase):
            return callback()

    def bind_pr(self, pr: dict):
        ticket = self.ticket()
        self.fence.bind_pr(ticket, pr)
        ticket["owned_pr"] = dict(pr)
        atomic_json(self.ticket_file, ticket)


def job_adapter(registry: Path, project: str, checkout: Path):
    import yaml
    config = registered_configuration(registry, project)
    if config is None:
        return None
    def git(directory, *args):
        return subprocess.run(["git", "-C", str(directory), *args], check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    common = git(registry.parent, "rev-parse", "HEAD")
    source = os.environ.get("START_ACTIVE_HEAD")
    if not sha40(common) or not sha40(source):
        raise FenceError("EXACT_SOURCE_OR_POLICY_IDENTITY_MISSING")
    def read(endpoint):
        env = os.environ.copy()
        credential = (env.get("RUNNER_TOKEN") if endpoint.startswith(
            "repos/" + config["runner_repository"] + "/"
        ) else env.get("PRODUCT_TOKEN") or env.get("GH_TOKEN"))
        if not credential:
            raise FenceError("EXISTING_READ_CREDENTIAL_UNAVAILABLE")
        env["GH_TOKEN"] = credential
        result = subprocess.run(["gh", "api", "--method", "GET", endpoint],
                                env=env, check=True, capture_output=True, text=True, timeout=20)
        return json.loads(result.stdout)
    own = {"repository": os.environ["GITHUB_REPOSITORY"],
           "run_id": int(os.environ["GITHUB_RUN_ID"]),
           "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
    inventory = read(f"repos/{own['repository']}/actions/runs/{own['run_id']}/jobs"
                     "?filter=latest&per_page=100")
    current_jobs = inventory.get("jobs", [])
    if inventory.get("total_count") != len(current_jobs):
        raise FenceError("OWN_JOB_INVENTORY_INCOMPLETE")
    matches = [j for j in current_jobs if j.get("name") == config["project_job_names"][project]
               and j.get("runner_name") == os.environ.get("RUNNER_NAME")
               and j.get("status") == "in_progress"
               and j.get("run_attempt") == own["attempt"]]
    if len(matches) != 1:
        raise FenceError("EXACT_CURRENT_RUN_JOB_REQUIRED")
    own["job_id"] = matches[0]["id"]
    binding = {"project": project, "repository": config["repository"],
               "branch": config["branch"], "remote_sha": source,
               "common_policy_sha": common, "product_policy_sha": os.environ["START_HEAD"],
               "backend_id": config["backend_id"], "run": own,
               "participant_revisions": config["participant_revisions"],
               "readonly_workflow_blobs": config.get("readonly_workflow_blobs", [])}
    config = dict(config)
    state = yaml.safe_load(git(checkout, "show", f"{source}:{config['state_path']}"))
    config["product_gate"] = state["next_action"]["type"]
    ticket_file = Path(os.environ["DW_JOB_ROOT"]) / "source-fence.json"
    if ticket_file.is_file():
        config["owned_pr"] = json.loads(ticket_file.read_text()).get("owned_pr")
    reader = GitHubEvidenceReader(binding, config, read)
    fence = SharedFence(Path(config["root"]), binding, reader)
    if fence.scope != "registered":
        raise FenceError("FIXTURE_BACKEND_CANNOT_ENABLE_ROUTE")
    adapter = DirectWorkerFence(fence, ticket_file)
    return adapter
