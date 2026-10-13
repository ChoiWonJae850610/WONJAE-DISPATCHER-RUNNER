#!/usr/bin/env python3
"""Shared, opt-in Development psql verification; no workflow/Owner gate is wired here."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

SHA = re.compile(r"[0-9a-f]{40}")
REF = re.compile(r"[a-z]{20}")
SQL_FILE = re.compile(r"supabase/tests/[a-z0-9][a-z0-9_-]*\.sql")
PLAN_FILE = re.compile(r"supabase/tests/[a-z0-9][a-z0-9_-]*\.json")
VARIABLE = re.compile(r"[a-z][a-z0-9_]{0,63}")
ERROR_MARKER = re.compile(r"[A-Z][A-Z0-9_]{3,100}")
PHASES = {"preflight", "runtime", "fixture_setup", "race_a", "race_b",
          "race_readback", "cleanup", "zero_residue"}
MANDATORY = {"preflight", "runtime", "cleanup", "zero_residue"}
RACE = {"fixture_setup", "race_a", "race_b", "race_readback"}
MAX_FILE = 256 * 1024
MAX_OUTPUT = 64 * 1024
STEP_TIMEOUT = 60
# The only currently registered GUC contract is CLASSMO Development G1-V.
# Do not accept plan-authored libpq options, alternate GUCs or arbitrary values.
CLASSMO_G1V_REF = "hfclkbqzxiqhcckwknih"
CLASSMO_G1V_TASK = "CLASSMO-EDITORIAL-G1V-FIXTURE-001"
CLASSMO_G1V_SETTINGS = {"classmo.g1v_authorized": CLASSMO_G1V_TASK}
# Supavisor session mode can accept the libpq startup options while failing
# to apply them on the PostgreSQL backend. Set the fixed GUC explicitly in
# *each* backend session before any pinned SQL, without changing credentials.
G1V_PGOPTIONS = "-c classmo.g1v_authorized=" + CLASSMO_G1V_TASK
G1V_SESSION_SET = "SET classmo.g1v_authorized = '" + CLASSMO_G1V_TASK + "';"
G1V_SESSION_CHECK = (
    "SELECT (current_setting('classmo.g1v_authorized', true) = '"
    + CLASSMO_G1V_TASK + "') AS wonjae_guc_ok \\gset\n"
    "\\echo __WONJAE_GUC_READY__:wonjae_guc_ok\n"
)


class VerificationBlocked(Exception):
    """Stable, nonsecret failure reason."""


def require(value: bool, code: str) -> None:
    if not value:
        raise VerificationBlocked(code)


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise VerificationBlocked("PSQL_GIT_UNAVAILABLE") from None
    require(proc.returncode == 0, "PSQL_GIT_UNVERIFIED")
    return proc.stdout.strip()


def exact_file(repo: Path, source_sha: str, rel: str, blob: str, *, plan: bool) -> Path:
    pattern = PLAN_FILE if plan else SQL_FILE
    require(pattern.fullmatch(rel) is not None and SHA.fullmatch(blob) is not None,
            "PSQL_PIN_INVALID")
    path = repo / rel
    require(path.is_file() and not path.is_symlink()
            and path.resolve().is_relative_to(repo.resolve()), "PSQL_FILE_UNSAFE")
    raw = path.read_bytes()
    require(0 < len(raw) <= MAX_FILE and b"\0" not in raw, "PSQL_FILE_UNSAFE")
    actual = hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\0" + raw
    ).hexdigest()
    require(actual == blob and _git(repo, "rev-parse", f"{source_sha}:{rel}") == blob,
            "PSQL_SOURCE_BLOB_CHANGED")
    if not plan:
        # Pinning alone is insufficient: psql metacommands can run local shell
        # commands with the Development database password in the environment.
        try:
            sql = raw.decode("utf-8")
        except UnicodeError:
            raise VerificationBlocked("PSQL_SCRIPT_ENCODING_INVALID") from None
        allowed = {"set", "if", "elif", "else", "endif", "echo", "gset", "pset"}
        for line in sql.splitlines():
            forbidden = (
                r"(?i)\\(?:!|setenv|gexec|copy|i|ir|o|w|cd|c|connect|"
                r"lo_export|lo_import)(?![a-z_])"
            )
            if re.search(forbidden, line):
                raise VerificationBlocked("PSQL_META_COMMAND_UNSAFE")
            stripped = line.lstrip()
            if stripped.startswith("\\"):
                found = re.match(r"\\([a-zA-Z_]+)", stripped)
                require(found is not None and found.group(1).lower() in allowed and
                        "`" not in stripped, "PSQL_META_COMMAND_UNSAFE")
        if re.search(r"(?im)^\s*\\set\s+ON_ERROR_STOP\s+(?:off|0|false)\b", sql):
            raise VerificationBlocked("PSQL_ERROR_STOP_OVERRIDE_UNSAFE")
        if re.search(r"(?is)\bcopy\b[^;]*\bprogram\b", sql):
            raise VerificationBlocked("PSQL_PROGRAM_EXECUTION_UNSAFE")
    return path


def load_plan(repo: Path, source_sha: str, project_ref: str,
              path: str, blob: str) -> dict[str, Any]:
    require(SHA.fullmatch(source_sha) is not None and REF.fullmatch(project_ref) is not None,
            "PSQL_IDENTITY_INVALID")
    require(_git(repo, "rev-parse", "HEAD") == source_sha, "PSQL_SOURCE_HEAD_CHANGED")
    source = exact_file(repo, source_sha, path, blob, plan=True)
    try:
        plan = json.loads(source.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise VerificationBlocked("PSQL_PLAN_INVALID") from None
    require(isinstance(plan, dict) and plan.get("schema_version") == 1 and
            plan.get("environment") == "development" and
            plan.get("project_ref") == project_ref, "PSQL_PLAN_IDENTITY_MISMATCH")
    phases = plan.get("phases")
    require(isinstance(phases, dict) and MANDATORY.issubset(phases) and
            set(phases).issubset(PHASES), "PSQL_PLAN_PHASES_INVALID")
    require(not (set(phases) & RACE) or RACE.issubset(phases),
            "PSQL_RACE_PHASES_INCOMPLETE")
    for value in phases.values():
        # Both contracts are supported, but the pin is always checked against
        # the exact source commit AND Git's on-disk blob. Never trust either
        # manifest spelling as proof that the SQL matches the source SHA.
        require(isinstance(value, dict) and
                set(value) in ({"path", "blob"}, {"path", "git_blob"}) and
                isinstance(value["path"], str), "PSQL_PHASE_PIN_INVALID")
        pinned_blob = value.get("blob", value.get("git_blob"))
        require(isinstance(pinned_blob, str), "PSQL_PHASE_PIN_INVALID")
        exact_file(repo, source_sha, value["path"], pinned_blob, plan=False)
    variables = plan.get("variables", {})
    require(isinstance(variables, dict) and len(variables) <= 20,
            "PSQL_VARIABLES_INVALID")
    for key, value in variables.items():
        require(isinstance(key, str) and VARIABLE.fullmatch(key) is not None and
                not any(word in key for word in ("password", "secret", "token", "credential")) and
                isinstance(value, str) and 0 < len(value) <= 4096 and
                not any(c in value for c in ("\0", "\r", "\n")),
                "PSQL_VARIABLES_INVALID")
    marker = plan.get("expected_b_error")
    require(marker is None or
            (isinstance(marker, str) and ERROR_MARKER.fullmatch(marker) is not None),
            "PSQL_RACE_EXPECTATION_INVALID")
    require(marker is None or RACE.issubset(phases), "PSQL_RACE_EXPECTATION_INVALID")
    registered_session_settings(plan)
    return plan


def registered_session_settings(plan: dict[str, Any]) -> dict[str, str]:
    """Only the approved CLASSMO G1-V custom GUC may reach libpq sessions."""
    contract = plan.get("execution_contract", {})
    require(isinstance(contract, dict), "PSQL_SESSION_CONTRACT_INVALID")
    required = contract.get("required_session_settings", {})
    require(isinstance(required, dict), "PSQL_SESSION_SETTINGS_INVALID")
    if required:
        require(plan.get("project_ref") == CLASSMO_G1V_REF and
                plan.get("source_task_id") == CLASSMO_G1V_TASK and
                required == CLASSMO_G1V_SETTINGS and
                contract.get("session_user") == "postgres" and
                contract.get("psql_on_error_stop") is True and
                contract.get("distinct_backend_sessions") is True and
                contract.get("race_isolation") == "read committed",
                "PSQL_SESSION_SETTINGS_NOT_REGISTERED")
    return dict(required)


def registered_target(project_ref: str, mode: str, pooler_host: str) -> tuple[str, str]:
    """Build one exact reviewed Development endpoint without credentials."""
    require(REF.fullmatch(project_ref) is not None, "PSQL_IDENTITY_INVALID")
    if mode == "direct":
        require(pooler_host == "", "PSQL_UNEXPECTED_POOLER_HOST")
        return f"db.{project_ref}.supabase.co", "postgres"
    require(mode == "session_pooler", "PSQL_CONNECTION_MODE_NOT_REGISTERED")
    # This IPv4 endpoint was read directly from the CLASSMO Development
    # Session Pooler connection parameters, not inferred from a region.
    require(project_ref == CLASSMO_G1V_REF and
            pooler_host == "aws-0-ap-northeast-2.pooler.supabase.com",
            "PSQL_POOLER_TARGET_NOT_REGISTERED")
    return pooler_host, f"postgres.{project_ref}"


def connection_env(project_ref: str, settings: dict[str, str], *,
                   session_settings: dict[str, str] | None = None,
                   expected_target: tuple[str, str] | None = None) -> dict[str, str]:
    host = settings.get("WONJAE_DEV_PGHOST", "")
    user = settings.get("WONJAE_DEV_PGUSER", "")
    port = settings.get("WONJAE_DEV_PGPORT", "")
    database = settings.get("WONJAE_DEV_PGDATABASE", "")
    password = settings.get("WONJAE_DEV_PGPASSWORD", "")
    cert = settings.get("WONJAE_DEV_PGSSLROOTCERT", "")
    direct = host == f"db.{project_ref}.supabase.co" and user == "postgres"
    session_pool = (
        re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", host) is not None
        and user == f"postgres.{project_ref}"
    )
    require(direct or session_pool, "PSQL_TARGET_NOT_PROJECT_SCOPED")
    if expected_target is not None:
        require((host, user) == expected_target,
                "PSQL_TARGET_NOT_REGISTERED")
    require(port == "5432" and database == "postgres" and bool(password),
            "PSQL_CONNECTION_FIELDS_INVALID")
    requested = session_settings if session_settings is not None else {}
    require(isinstance(requested, dict) and
            (not requested or (project_ref == CLASSMO_G1V_REF and
                               requested == CLASSMO_G1V_SETTINGS)),
            "PSQL_SESSION_SETTINGS_NOT_REGISTERED")
    certificate = Path(cert)
    require(bool(cert) and certificate.is_file() and
            not certificate.is_symlink(), "PSQL_SSL_CERT_MISSING")
    # Only explicit, reviewed libpq fields are inherited. Never pass a URL or
    # password as a command-line argument, return value or evidence field.
    env = {key: settings[key] for key in ("PATH", "HOME", "SYSTEMROOT")
           if key in settings}
    env.update({
        "PGHOST": host, "PGPORT": port, "PGUSER": user, "PGDATABASE": database,
        "PGPASSWORD": password, "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": str(certificate.resolve()),
        "PGCONNECT_TIMEOUT": "10", "PGAPPNAME": "wonjae-dev-psql-verify",
        "PGCLIENTENCODING": "UTF8",
    })
    if requested:
        # Fixed literal, NOT untrusted plan values. Supavisor may not carry
        # startup options into a backend; the verifier also issues explicit
        # session-level SET before every preflight, race and cleanup script.
        env["PGOPTIONS"] = G1V_PGOPTIONS
    return env


def fixed_session_initializer(env: dict[str, str]) -> str:
    """Only the audited CLASSMO setting, never untrusted connection options."""
    startup = env.get("PGOPTIONS", "")
    require(startup in ("", G1V_PGOPTIONS), "PSQL_SESSION_SETTINGS_NOT_REGISTERED")
    return G1V_SESSION_SET if startup else ""


def psql_failure_code(stderr: str) -> str:
    """Classify known PostgreSQL failures without printing raw stderr or secrets."""
    for fragment, result in (
        ("EDITORIAL_G1V_AUTHORIZATION_REQUIRED", "PSQL_G1V_AUTHORIZATION_FAILED"),
        ("CLASSMO_NONPRODUCTION_GUARD_FAILED", "PSQL_DEVELOPMENT_GUARD_FAILED"),
        ("password authentication failed", "PSQL_PASSWORD_AUTHENTICATION_FAILED"),
        ("certificate verify failed", "PSQL_TLS_VERIFY_FAILED"),
        ("could not translate host name", "PSQL_DATABASE_DNS_FAILED"),
    ):
        if fragment in stderr:
            return result
    return "PSQL_SCRIPT_FAILED"


def psql_command(vars_: dict[str, str], *, interactive: bool = False) -> list[str]:
    binary = shutil.which("psql")
    require(binary is not None, "PSQL_BINARY_UNAVAILABLE")
    cmd = [binary, "-X", "-qAt", "-P", "pager=off",
           "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=terse"]
    for key, value in sorted(vars_.items()):
        cmd.extend(["-v", f"{key}={value}"])
    if interactive:
        cmd += ["-f", "-"]
    return cmd


class PsqlRunner:
    def __init__(self, env: dict[str, str], variables: dict[str, str]):
        self.env = env
        self.variables = variables
        self.initial_sql = fixed_session_initializer(env)

    def _init_commands(self) -> list[str]:
        return ["-c", self.initial_sql] if self.initial_sql else []

    def _exec(self, args: list[str], timeout: int = STEP_TIMEOUT) -> str:
        try:
            result = subprocess.run(
                args, env=self.env, capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise VerificationBlocked("PSQL_EXECUTION_UNVERIFIED") from None
        if result.returncode != 0:
            raise VerificationBlocked(psql_failure_code(result.stderr)) from None
        require(len(result.stdout) <= MAX_OUTPUT, "PSQL_SCRIPT_FAILED")
        return result.stdout.strip()

    def file(self, path: Path) -> None:
        self._exec(
            psql_command(self.variables) + self._init_commands()
            + ["-f", str(path)]
        )

    def query(self, sql: str) -> str:
        return self._exec(
            psql_command({}) + self._init_commands() + ["-c", sql],
            timeout=15,
        )

    def new_session(self) -> "PsqlSession":
        return PsqlSession(self.env, self.variables, self.initial_sql)

    def race(self, a: Path, b: Path, readback: Path,
             expected_error: str | None, probe_seconds: int = 12) -> None:
        first = second = None
        try:
            first = self.new_session()
            first_pid = first.pid()
            first.include(a, "__WONJAE_A_READY__")
            idle = (
                "select state = 'idle in transaction' from pg_catalog.pg_stat_activity "
                f"where pid={first_pid};"
            )
            require(self.query(idle) == "t", "PSQL_RACE_A_TRANSACTION_NOT_HELD")
            second = self.new_session()
            second_pid = second.pid()
            require(first_pid != second_pid, "PSQL_RACE_SAME_SESSION")
            second.include(b, "__WONJAE_B_DONE__", wait=False)
            blocked_sql = (
                "select pg_catalog.pg_blocking_pids("
                f"{second_pid}) @> ARRAY[{first_pid}]::integer[];"
            )
            deadline = time.monotonic() + probe_seconds
            while time.monotonic() < deadline:
                if self.query(blocked_sql) == "t":
                    break
                require(second.alive(), "PSQL_RACE_B_FINISHED_WITHOUT_CONTENTION")
                time.sleep(0.25)
            else:
                raise VerificationBlocked("PSQL_RACE_LOCK_NOT_OBSERVED")
            first.send("COMMIT;\n\\echo __WONJAE_A_COMMITTED__\n")
            first.expect("__WONJAE_A_COMMITTED__", 10)
            first.finish(expected=0)
            if expected_error:
                second.finish(expected=3, marker=expected_error)
            else:
                second.expect("__WONJAE_B_DONE__", 10)
                second.finish(expected=0)
            self.file(readback)
        finally:
            # A pinned cleanup phase also runs after any of these failures.
            # Never let a dangling transaction survive after a timeout.
            if second is not None:
                second.abort()
            if first is not None:
                first.abort()


class PsqlSession:
    def __init__(self, env: dict[str, str], variables: dict[str, str],
                 initial_sql: str = ""):
        require(initial_sql in ("", G1V_SESSION_SET),
                "PSQL_SESSION_SETTINGS_NOT_REGISTERED")
        try:
            self.proc = subprocess.Popen(
                psql_command(variables, interactive=True), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
            )
        except OSError:
            raise VerificationBlocked("PSQL_SESSION_START_FAILED") from None
        self.lines: queue.Queue[str] = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read, args=(self.proc.stdout, self.lines),
                         daemon=True).start()
        self.error_reader = threading.Thread(target=self._read_err, daemon=True)
        self.error_reader.start()
        if initial_sql:
            # Explicit SET + server-side readback is required for each A/B
            # backend. Startup PGOPTIONS alone is not proof on Supavisor.
            try:
                self.send(initial_sql + "\n" + G1V_SESSION_CHECK)
                require(self.expect("__WONJAE_GUC_READY__", 10) == "t",
                        "PSQL_SESSION_GUC_UNVERIFIED")
            except VerificationBlocked:
                self.abort()
                raise

    @staticmethod
    def _read(pipe: Any, output: queue.Queue[str]) -> None:
        for line in pipe:
            output.put(line.strip()[:256])

    def _read_err(self) -> None:
        for line in self.proc.stderr:
            if len(self.stderr) < 200:
                self.stderr.append(line[:512])

    def alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, command: str) -> None:
        require(self.proc.stdin is not None and self.alive(), "PSQL_SESSION_STOPPED")
        try:
            self.proc.stdin.write(command)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise VerificationBlocked("PSQL_SESSION_WRITE_FAILED") from None

    def expect(self, prefix: str, timeout: int) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=min(0.15, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                require(self.alive() or not self.lines.empty(),
                        "PSQL_SESSION_ENDED_BEFORE_BARRIER")
                continue
            if line.startswith(prefix):
                return line[len(prefix):]
        raise VerificationBlocked("PSQL_SESSION_BARRIER_TIMEOUT")

    def pid(self) -> int:
        self.send(
            "select pg_backend_pid() as wonjae_pid \\gset\n"
            "\\echo __WONJAE_PID__:wonjae_pid\n"
        )
        raw = self.expect("__WONJAE_PID__", 10)
        require(re.fullmatch(r"[1-9][0-9]{0,9}", raw) is not None,
                "PSQL_SESSION_PID_UNVERIFIED")
        return int(raw)

    def include(self, path: Path, barrier: str, *, wait: bool = True) -> None:
        safe = str(path.resolve()).replace("\\", "/")
        require("'" not in safe and "\n" not in safe, "PSQL_INCLUDE_PATH_INVALID")
        self.send(f"\\i '{safe}'\n\\echo {barrier}\n")
        if wait:
            self.expect(barrier, 30)

    def finish(self, *, expected: int, marker: str | None = None) -> None:
        if expected == 0:
            self.send("\\q\n")
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
            code = self.proc.wait(timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            raise VerificationBlocked("PSQL_SESSION_FINISH_TIMEOUT") from None
        self.error_reader.join(timeout=2)
        require(code == expected, "PSQL_RACE_UNEXPECTED_SESSION_RESULT")
        if marker is not None:
            require(marker in "".join(self.stderr), "PSQL_RACE_CONFLICT_NOT_PROVED")

    def abort(self) -> None:
        if self.alive():
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait(timeout=5)


def verify(plan: dict[str, Any], repo: Path, source_sha: str,
           runner: PsqlRunner) -> dict[str, Any]:
    phases = {key: repo / val["path"] for key, val in plan["phases"].items()}
    receipt = {
        "result": "FAILED", "preflight": "NOT_RUN", "runtime": "NOT_RUN",
        "race": "NOT_RUN", "cleanup": "NOT_RUN", "zero_residue": "NOT_RUN",
        "source_sha": source_sha, "project_ref": plan["project_ref"],
        "error_code": None,
    }
    touched = False
    try:
        runner.file(phases["preflight"])
        receipt["preflight"] = "PASS"
        touched = True
        runner.file(phases["runtime"])
        receipt["runtime"] = "PASS"
        if RACE.issubset(phases):
            runner.file(phases["fixture_setup"])
            runner.race(phases["race_a"], phases["race_b"],
                        phases["race_readback"], plan.get("expected_b_error"))
            receipt["race"] = "PASS"
    except VerificationBlocked as exc:
        receipt["error_code"] = str(exc)
    finally:
        # Cleanup must be independently checked even when runtime or race failed.
        # A failed guard forbids any subsequent potentially mutating cleanup.
        if touched:
            try:
                runner.file(phases["cleanup"])
                receipt["cleanup"] = "PASS"
            except VerificationBlocked:
                receipt["cleanup"] = "FAILED"
                receipt["error_code"] = "PSQL_CLEANUP_FAILED"
            try:
                runner.file(phases["zero_residue"])
                receipt["zero_residue"] = "PASS"
            except VerificationBlocked:
                receipt["zero_residue"] = "FAILED"
                receipt["error_code"] = "PSQL_ZERO_RESIDUE_UNVERIFIED"
    if (receipt["preflight"] == "PASS" and receipt["runtime"] == "PASS"
            and receipt["cleanup"] == "PASS"
            and receipt["zero_residue"] == "PASS"
            and (receipt["race"] == "PASS" or not RACE.issubset(phases))):
        receipt["result"] = "PSQL_DEVELOPMENT_VERIFIED"
        receipt["error_code"] = None
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    for option in ("repo", "source-sha", "project-ref", "plan-path",
                   "plan-blob", "evidence", "summary"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--require-race", action="store_true")
    parser.add_argument("--connection-mode", default="direct")
    parser.add_argument("--pooler-host", default="")
    args = parser.parse_args()
    try:
        target = registered_target(args.project_ref, args.connection_mode,
                                   args.pooler_host)
        repo = Path(args.repo).resolve()
        plan = load_plan(repo, args.source_sha, args.project_ref,
                         args.plan_path, args.plan_blob)
        if args.require_race:
            require(RACE.issubset(plan["phases"]) and
                    bool(plan.get("expected_b_error")),
                    "PSQL_INDEPENDENT_RACE_NOT_REGISTERED")
        if not args.execute:
            print("PSQL_VERIFIER=SOURCE_PLAN_VALIDATED_ONLY")
            return 0
        require(os.getenv("WONJAE_OWNER_PSQL_APPROVED") == "yes",
                "PSQL_OWNER_WORKFLOW_APPROVAL_MISSING")
        env = connection_env(
            args.project_ref, dict(os.environ),
            session_settings=registered_session_settings(plan),
            expected_target=target,
        )
        result = verify(plan, repo, args.source_sha,
                        PsqlRunner(env, plan.get("variables", {})))
        evidence = Path(args.evidence)
        evidence.write_text(
            json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        evidence.chmod(0o600)
        with Path(args.summary).open("a", encoding="utf-8") as output:
            output.write(
                "\n## Registered Development psql verification\n"
                f"- preflight: {result['preflight']}\n"
                f"- runtime: {result['runtime']}\n"
                f"- distinct-session lock contention: {result['race']}\n"
                f"- cleanup: {result['cleanup']}\n"
                f"- zero residue: {result['zero_residue']}\n"
                "- Production, actual device, release: NOT_RUN\n"
            )
        if result["result"] != "PSQL_DEVELOPMENT_VERIFIED":
            print("::error::" + (result["error_code"] or "PSQL_TEST_UNVERIFIED"))
            return 1
        return 0
    except VerificationBlocked as exc:
        print(f"::error::{exc}")
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print("::error::PSQL_RESULT_UNVERIFIED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
