"""Local cross-route fence. No daemon, credential store, task queue or network on import.

The same implementation runs through Windows UNC and native WSL paths. A durable
mkdir transaction and a persisted grant are BOTH required. An abandoned transaction,
expired grant or uncertain operation is never automatically reclaimed.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

CONTRACT = "cross_route_writer_fence_v1"
PARTICIPANTS = (
    "direct_worker", "document_merge", "reconciliation", "source_recovery",
    "provider_state_update", "watch",
)
ACTIVE = {"queued", "in_progress", "waiting", "requested", "pending"}
SOURCE_PHASES = {"before_source", "before_commit", "before_push", "before_pr", "before_merge"}


class FenceError(RuntimeError):
    pass


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def sha40(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{40}", value))


def atomic_json(file: Path, value: object) -> None:
    temporary = file.with_name(file.name + ".tmp-" + secrets.token_hex(16))
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, file)
    if os.name != "nt":
        fd = os.open(file.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def native_identity(pid: int | None = None) -> dict:
    pid = os.getpid() if pid is None else int(pid)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        values = [wintypes.FILETIME() for _ in range(4)]
        handle = kernel.GetCurrentProcess() if pid == os.getpid() else kernel.OpenProcess(
            0x1000, False, pid,
        )
        if not handle:
            raise FenceError("WINDOWS_PROCESS_IDENTITY_UNAVAILABLE")
        try:
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
                raise FenceError("WINDOWS_PROCESS_IDENTITY_UNAVAILABLE")
        finally:
            if pid != os.getpid():
                kernel.CloseHandle(handle)
        created = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
        return {"os": "windows", "pid": pid, "birth": str(created)}
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"os": "linux", "pid": pid, "uid": Path(f"/proc/{pid}").stat().st_uid,
            "birth": fields[19],
            "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def process_terminal(identity: dict) -> bool:
    """Local OS proof only; PID absence alone never reconciles run/publication."""
    if identity.get("os") == "linux" and os.name != "nt":
        if identity.get("boot") != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
            return False
        file = Path(f"/proc/{int(identity['pid'])}/stat")
        try:
            fields = file.read_text().rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return True
        return fields[19] != identity.get("birth") or fields[0] == "Z"
    if identity.get("os") == "windows" and os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(identity["pid"]))
        if not handle:
            return ctypes.get_last_error() == 87  # invalid PID, never access-denied
        try:
            values = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
                return False
            born = str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return born != identity.get("birth") or code.value != 259
        finally:
            kernel.CloseHandle(handle)
    return False


def inspect_registered_backend(root: Path, marker: dict) -> dict:
    """Read native identity even from Windows; no mount or settings mutation."""
    script = (
        "import pathlib,json,os,sys; p=pathlib.Path(sys.argv[1]); s=p.stat(); "
        "print(json.dumps({'inode':s.st_ino,'uid':s.st_uid,'mode':s.st_mode & 511,"
        "'path':str(p.resolve()),'boot':pathlib.Path('/proc/sys/kernel/random/boot_id')"
        ".read_text().strip()}))"
    )
    if os.name == "nt":
        expected_anchor = "\\\\wsl.localhost\\" + marker.get("distro", "") + "\\"
        if root.anchor.lower() != expected_anchor.lower():
            raise FenceError("REGISTERED_BACKEND_MUST_BE_VERIFIED_WSL_UNC")
        native = "/" + "/".join(root.parts[1:])
        if native != marker.get("native_root"):
            raise FenceError("NATIVE_ROOT_MAPPING_CHANGED")
        result = subprocess.run(
            ["wsl.exe", "-d", marker["distro"], "--exec", "python3", "-c", script, native],
            check=True, capture_output=True, text=True, timeout=10,
        )
        info = json.loads(result.stdout)
    else:
        result = subprocess.run(["python3", "-c", script, str(root)], check=True,
                                capture_output=True, text=True, timeout=10)
        info = json.loads(result.stdout)
    if any(info.get(k) != expected for k, expected in {
        "inode": marker.get("root_inode"), "uid": marker.get("native_uid"), "mode": 0o700,
        "path": marker.get("native_root"), "boot": marker.get("boot_id"),
    }.items()) or root.stat().st_ino != info["inode"]:
        raise FenceError("BACKEND_REBOOT_IDENTITY_OR_PERMISSION_CHANGED")
    return info


def validate_evidence(binding: dict, facts: dict, now: float, *, owned_pr=None,
                      source_required=True) -> None:
    for key in ("project", "repository", "branch", "common_policy_sha", "product_policy_sha"):
        if facts.get(key) != binding.get(key):
            raise FenceError("EVIDENCE_BINDING_CHANGED:" + key)
    if not isinstance(facts.get("observed_at"), (int, float)) or not (
        now - 30 <= facts["observed_at"] <= now + 1
    ):
        raise FenceError("EVIDENCE_STALE")
    if any(facts.get(key) is not True for key in (
        "runs_complete", "jobs_complete", "prs_complete", "runner_reconciled",
    )):
        raise FenceError("INVENTORY_INCOMPLETE_OR_RUNNER_UNCERTAIN")
    if facts.get("remote_sha") != binding["remote_sha"] or not sha40(facts.get("remote_sha")):
        raise FenceError("REMOTE_SHA_CHANGED")
    if source_required and facts.get("product_gate") != "SOURCE_READY":
        raise FenceError("PRODUCT_GATE_BLOCKED")
    for name in PARTICIPANTS:
        participant = facts.get("participants", {}).get(name, {})
        expected = binding.get("participant_revisions", {}).get(name)
        if not sha40(expected) or participant != {"revision": expected, "enforced": True}:
            raise FenceError("UNFENCED_PARTICIPANT:" + name)
    for key in ("runs", "jobs", "open_prs"):
        if not isinstance(facts.get(key), list):
            raise FenceError("INVENTORY_INVALID")
    for item in [*facts["runs"], *facts["jobs"]]:
        status = item.get("status")
        if status not in ACTIVE | {"completed"}:
            raise FenceError("UNKNOWN_RUN_OR_JOB_STATUS")
        if status == "completed":
            continue
        project = item.get("project")
        if item.get("scope_attested") is not True:
            raise FenceError("UNKNOWN_ACTIVE_SCOPE")
        if project is not None and project != binding["project"]:
            continue
        own = binding.get("run")
        if own and item.get("run") == own and item.get("fenced_entry") is True:
            continue
        if item.get("read_only") is True and item.get("workflow_blob") in binding.get(
            "readonly_workflow_blobs", []
        ):
            continue
        raise FenceError("ACTIVE_OR_QUEUED_WRITER")
    for pr in facts["open_prs"]:
        if pr.get("base") != binding["branch"]:
            continue
        if owned_pr and pr == owned_pr:
            continue
        raise FenceError("CONFLICTING_PR")


class SharedFence:
    def __init__(self, root: Path, binding: dict, reader: Callable, *, clock=time.time):
        self.root = root
        self.binding = dict(binding)
        self.reader = reader
        self.clock = clock
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", binding.get("project", "")):
            raise FenceError("PROJECT_INVALID")
        if not sha40(binding.get("remote_sha")):
            raise FenceError("BASE_SHA_INVALID")
        if not root.is_dir() or root.is_symlink():
            raise FenceError("EXPLICIT_PRIVATE_ROOT_REQUIRED")
        marker = json.loads((root / "backend.json").read_text())
        if marker.get("backend_id") != binding.get("backend_id"):
            raise FenceError("BACKEND_BINDING_CHANGED")
        if os.name != "nt":
            info = root.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise FenceError("PRIVATE_ROOT_OWNERSHIP_REQUIRED")
        self.scope = marker.get("evidence_scope")
        if self.scope not in {"fixture", "registered"}:
            raise FenceError("BACKEND_SCOPE_INVALID")
        if self.scope == "registered":
            inspect_registered_backend(root, marker)
        self.directory = root / binding["project"].lower()
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink():
            raise FenceError("LINKED_PROJECT_STORE")
        self.file = self.directory / "state.json"
        self.mutex = self.directory / "transaction"

    @contextlib.contextmanager
    def transaction(self):
        try:
            self.mutex.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise FenceError("TRANSACTION_HELD_OR_ABANDONED_NO_TAKEOVER") from exc
        clean = False
        try:
            atomic_json(self.mutex / "owner.json", native_identity())
            state = json.loads(self.file.read_text()) if self.file.exists() else {
                "contract": CONTRACT, "backend_id": self.binding["backend_id"],
                "project": self.binding["project"], "repository": self.binding["repository"],
                "branch": self.binding["branch"], "epoch": 0, "holder": None,
                "consumed": {}, "quarantine": None, "history": [],
            }
            for key in ("project", "repository", "branch", "backend_id"):
                if state.get(key) != self.binding[key]:
                    raise FenceError("COPIED_OR_FOREIGN_STATE")
            try:
                yield state
            finally:
                atomic_json(self.file, state)
                clean = True
        finally:
            if clean:
                (self.mutex / "owner.json").unlink()
                self.mutex.rmdir()
            # Any interrupted/failed write preserves the exact transaction.

    def acquire(self, request: dict) -> dict:
        with self.transaction() as state:
            if state["quarantine"]:
                raise FenceError("QUARANTINED_NO_TAKEOVER")
            if state["holder"]:
                if state["holder"]["expires_at"] <= self.clock():
                    state["quarantine"] = "EXPIRED_OWNER_UNRECONCILED"
                raise FenceError("WRITER_HELD_NO_TAKEOVER")
            task = request.get("task_id", "")
            if not re.fullmatch(re.escape(self.binding["project"]) + r"-[A-Za-z0-9_-]{1,80}", task):
                raise FenceError("TASK_PROJECT_INVALID")
            if request.get("route") not in {"watch", "direct_worker", "direct_local"}:
                raise FenceError("SOURCE_ROUTE_INVALID")
            request_id = request.get("request_id", "")
            if not re.fullmatch(r"[a-f0-9]{32}", request_id):
                raise FenceError("REQUEST_ID_INVALID")
            if not self.clock() < request.get("expires_at", 0) <= self.clock() + 900:
                raise FenceError("REQUEST_EXPIRED_OR_TTL_INVALID")
            if state["consumed"].get(task) or state["consumed"].get(request_id):
                raise FenceError("ALREADY_CONSUMED")
            ttl = request.get("lease_seconds", 60)
            if not isinstance(ttl, int) or not 1 <= ttl <= 4500:
                raise FenceError("LEASE_DEADLINE_INVALID")
            facts = self.reader()
            validate_evidence(self.binding, facts, self.clock())
            state["epoch"] += 1
            holder = {**self.binding, "contract": CONTRACT, "task_id": task,
                      "route": request["route"], "request_id": request_id,
                      "request_expires_at": request["expires_at"],
                      "epoch": state["epoch"], "lease_id": secrets.token_hex(16),
                      "issued_at": self.clock(), "expires_at": self.clock() + ttl,
                      "owner": native_identity(request.get("supervisor_pid")),
                      "owned_pr": None, "operation": None,
                      "evidence_scope": self.scope}
            state["holder"] = holder
            state["consumed"].update({task: holder["epoch"], request_id: holder["epoch"]})
            state["history"].append({"event": "ACQUIRED", "epoch": holder["epoch"],
                                     "task_id": task, "route": holder["route"]})
            return holder.copy()

    def _same(self, state: dict, ticket: dict) -> dict:
        holder = state["holder"]
        keys = ("project", "repository", "branch", "backend_id", "task_id", "route",
                "remote_sha", "common_policy_sha", "product_policy_sha",
                "participant_revisions", "epoch", "lease_id", "run")
        if not holder or any(holder.get(k) != ticket.get(k) for k in keys):
            raise FenceError("STALE_OR_FOREIGN_OWNER")
        if state["quarantine"]:
            raise FenceError("QUARANTINED_NO_MUTATION")
        if holder["expires_at"] <= self.clock():
            state["quarantine"] = "LEASE_EXPIRED"
            raise FenceError("LEASE_EXPIRED_NO_TAKEOVER")
        return holder

    @contextlib.contextmanager
    def guarded(self, ticket: dict, phase: str):
        if phase not in SOURCE_PHASES | {"local_record", "release"}:
            raise FenceError("SOURCE_ONLY_PHASE_REQUIRED")
        with self.transaction() as state:
            holder = self._same(state, ticket)
            if holder["operation"] is not None:
                raise FenceError("PRIOR_OPERATION_UNRECONCILED")
            try:
                expected = dict(self.binding)
                if holder.get("integrated_sha"):
                    if phase != "release":
                        raise FenceError("INTEGRATED_TASK_IS_TERMINAL")
                    expected["remote_sha"] = holder["integrated_sha"]
                validate_evidence(expected, self.reader(), self.clock(),
                                  owned_pr=holder["owned_pr"],
                                  source_required=not holder.get("integrated_sha"))
            except FenceError as exc:
                state["quarantine"] = str(exc)
                raise
            holder["operation"] = {"phase": phase, "parent": native_identity(),
                                   "started_at": self.clock()}
            # Persist BEFORE any protected child/commit/push/PR callback.
            atomic_json(self.file, state)
            try:
                yield holder
            except BaseException:
                state["quarantine"] = "CHILD_OR_PUBLICATION_UNCERTAIN"
                raise
            else:
                holder["operation"] = None
                state["history"].append({"event": phase, "epoch": holder["epoch"]})

    def verify(self, ticket: dict, phase: str) -> dict:
        if phase == "local_record":
            with self.transaction() as state:
                holder = self._same(state, ticket)
                return self._proof(holder, fresh=False)
        with self.guarded(ticket, phase) as holder:
            return self._proof(holder, fresh=True)

    def _proof(self, holder: dict, *, fresh: bool) -> dict:
        return {"contract": CONTRACT, "evidence_scope": self.scope,
                    "project": holder["project"], "repository": holder["repository"],
                    "branch": holder["branch"], "task_id": holder["task_id"],
                    "route": holder["route"], "epoch": holder["epoch"],
                    "lease_id": holder["lease_id"], "source_sha": holder["remote_sha"],
                    "common_policy_sha": holder["common_policy_sha"],
                    "observed_at": int(self.clock() * 1000),
                    "expires_at": int(min(holder["expires_at"], self.clock() + 60) * 1000),
                    "all_mutating_participants_enforced": True,
                    "complete_fresh_run_pr_inventory": fresh, "owned_grant_valid": True}

    def begin_child(self, ticket: dict, supervisor_pid: int) -> None:
        with self.guarded(ticket, "before_source"):
            pass
        with self.transaction() as state:
            holder = self._same(state, ticket)
            if holder["operation"] is not None:
                raise FenceError("PRIOR_OPERATION_UNRECONCILED")
            holder["operation"] = {"phase": "child_reserved", "child": None,
                                   "parent": native_identity(supervisor_pid)}

    def attach_child(self, ticket: dict, pid: int) -> None:
        with self.transaction() as state:
            holder = self._same(state, ticket)
            if not holder["operation"] or holder["operation"]["phase"] != "child_reserved":
                raise FenceError("EXACT_CHILD_RESERVATION_REQUIRED")
            holder["operation"]["child"] = native_identity(pid)
            holder["operation"]["phase"] = "child_running"

    def finish_child(self, ticket: dict) -> None:
        with self.transaction() as state:
            holder = self._same(state, ticket)
            operation = holder["operation"]
            if not operation or not operation.get("child") or not process_terminal(
                operation["child"],
            ):
                raise FenceError("EXACT_CHILD_TERMINATION_UNVERIFIED")
            state["history"].append({"event": "CHILD_TERMINAL", "child": operation["child"],
                                     "epoch": holder["epoch"]})
            holder["operation"] = None

    def bind_pr(self, ticket: dict, pr: dict) -> None:
        with self.transaction() as state:
            holder = self._same(state, ticket)
            if set(pr) != {"number", "base", "head", "task_id"} or (
                pr["base"] != holder["branch"] or not sha40(pr["head"])
                or pr["task_id"] != holder["task_id"] or not isinstance(pr["number"], int)
            ):
                raise FenceError("PR_RECEIPT_INVALID")
            if holder["owned_pr"] and holder["owned_pr"]["number"] != pr["number"]:
                raise FenceError("OWNED_PR_CHANGED")
            holder["owned_pr"] = dict(pr)

    def release(self, ticket: dict, receipt: dict) -> None:
        with self.guarded(ticket, "release"):
            if receipt != {"task_id": ticket["task_id"], "epoch": ticket["epoch"],
                           "child_terminal": True, "publication_reconciled": True}:
                raise FenceError("TERMINAL_RECEIPT_REQUIRED")
        with self.transaction() as state:
            self._same(state, ticket)
            state["holder"] = None
            state["history"].append({"event": "RELEASED", "epoch": ticket["epoch"]})

    def acknowledge_integration(self, ticket: dict, receipt: dict) -> None:
        with self.transaction() as state:
            holder = self._same(state, ticket)
            own = holder["owned_pr"]
            if not own or any(receipt.get(k) != value for k, value in {
                "repository": holder["repository"], "number": own["number"],
                "head": own["head"], "base": holder["branch"], "merged": True,
            }.items()) or not sha40(receipt.get("integrated_sha")):
                raise FenceError("EXACT_OWN_MERGE_RECEIPT_REQUIRED")
            facts = self.reader()
            expected = {**self.binding, "remote_sha": receipt["integrated_sha"]}
            validate_evidence(expected, facts, self.clock(), source_required=False)
            holder["integrated_sha"] = receipt["integrated_sha"]
            state["history"].append({"event": "OWN_INTEGRATION_ACK", "epoch": holder["epoch"]})

    def recover(self, ticket: dict, receipt: dict, probe: Callable) -> None:
        # Explicit trusted exact-run reconciliation only. Never called by acquire.
        with self.transaction() as state:
            holder = state["holder"]
            if not holder or any(holder.get(k) != ticket.get(k) for k in (
                "task_id", "epoch", "lease_id", "backend_id", "run",
            )):
                raise FenceError("RECOVERY_OWNER_MISMATCH")
            if holder["operation"]:
                raise FenceError("INTERRUPTED_TRANSACTION_REQUIRES_OFFLINE_RECONCILIATION")
            if receipt != {"task_id": holder["task_id"], "epoch": holder["epoch"],
                           "run": holder.get("run"), "run_jobs_terminal": True,
                           "publication_reconciled": True} or not probe(holder["owner"]):
                raise FenceError("EXACT_RUN_CHILD_PUBLICATION_PROOF_REQUIRED")
            facts = self.reader()
            exact_run = self.binding.get("run")
            if exact_run and facts.get("terminal_run") != {
                "run": exact_run, "status": "completed", "all_jobs_terminal": True,
            }:
                raise FenceError("EXACT_TERMINAL_RUN_READBACK_REQUIRED")
            if any(item.get("status") in ACTIVE and item.get("project") in {
                None, self.binding["project"],
            } for item in [*facts.get("runs", []), *facts.get("jobs", [])]):
                raise FenceError("RECOVERY_HAS_ACTIVE_OR_UNKNOWN_JOB")
            validate_evidence(self.binding, facts, self.clock())
            state["epoch"] += 1
            state["holder"] = None
            state["quarantine"] = None
            state["history"].append({"event": "EXACT_RECOVERY", "epoch": state["epoch"]})


class GitFixtureReader:
    """Offline executable evidence: real bare Git HEAD plus synthetic run/PR facts."""
    def __init__(self, binding: dict, git_directory: Path, facts_file: Path):
        self.binding, self.git_directory, self.facts_file = binding, git_directory, facts_file

    def __call__(self) -> dict:
        facts = json.loads(self.facts_file.read_text())
        result = subprocess.run(
            ["git", "-c", "safe.directory=" + str(self.git_directory),
             "--git-dir=" + str(self.git_directory), "rev-parse",
             "refs/heads/" + self.binding["branch"]],
            check=True, capture_output=True, text=True, timeout=10,
        )
        facts["remote_sha"] = result.stdout.strip()
        facts["observed_at"] = time.time()
        return facts
