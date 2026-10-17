"""One exact reviewed CLASSMO source-runtime check in the trusted Owner-launched parent.

Codex's intentionally network-denied source sandbox cannot start an Edge HTTP
listener. Never run the product-authored Python helper outside that sandbox.
The trusted parent instead runs this fixed harness against exact reviewed
source modules, a pinned local runtime and synthetic-only test fixtures.
No product/provider credentials enter the native process.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable

from .classmo_edge_preparation import (
    EDGE_ROOT,
    needs_classmo_edge_preparation,
    verify_classmo_pr_identity,
)
from .classmo_edge_runtime import SelectedEdgeRuntimeError

# A follow-up head must be reviewed before a different source revision is
# executed outside the Codex source sandbox. These are Git blob identities
# fetched from the existing exact CLASSMO PR #141, NOT a dynamic code loader.
REVIEWED_SOURCE_HEAD = "e5e74a0802f27a465d6697bc49b8e095a17692fc"
SOURCE_DIR = Path("supabase/functions/editorial-media-prepare")
REVIEWED_BLOBS = {
    "normalize.ts": "e9f0410d289aea2f6bd06b911d2f5504ceed6fb2",
    "test.ts": "44db9af520a849e1d82d227d11c45cd08d0012eb",
    "boundary-test.ts": "012bbfaca649dadd5d73edafdf90e4babc39bd29",
    "boundary.ts": "5e579f2a772f27b9e3427901ed76f35003640d10",
    "gateway.ts": "3149464a7898173f87a2fb1cff051297f41eaa64",
}
MARKER = b"CLASSMO_SELECTED_EDGE_SOURCE_TESTS_PASS_NOT_PROVIDER"
USER_MAIN = """
import { runTests } from "./test.ts";
import { boundaryTests } from "./boundary-test.ts";
const wasm = await Deno.readFile(new URL(
  "./vendor/dist/x86/magick.wasm", import.meta.url
));
const normalizer = await runTests(wasm);
const boundary = await boundaryTests();
if (normalizer !== "CLASSMO_NORMALIZATION_SYNTHETIC_PASS" ||
    boundary !== "CLASSMO_BOUNDARY_SYNTHETIC_PASS") {
  throw Error("selected source synthetic tests did not pass");
}
Deno.serve(() => new Response("CLASSMO_SELECTED_EDGE_SOURCE_TESTS_PASS_NOT_PROVIDER"));
"""
MAIN_SERVICE = """
const path = Deno.env.get("CLASSMO_SOURCE_USER_DIR");
if (!path) throw Error("source user path unavailable");
Deno.serve(async req => {
  const worker = await EdgeRuntime.userWorkers.create({
    servicePath: path,
    memoryLimitMb: 150,
    workerTimeoutMs: 60000,
    noModuleCache: false,
    envVars: [],
    forceCreate: false,
    cpuTimeSoftLimitMs: 20000,
    cpuTimeHardLimitMs: 40000,
    staticPatterns: ["**/*.wasm"],
    context: { useReadSyncFileAPI: true },
  });
  return worker.fetch(req);
});
"""


class SelectedSourceRuntimeError(RuntimeError):
    """Fixed, log-safe failure: never return source output or credentials."""


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _reviewed_source(repo: Path) -> dict[str, str]:
    """Only five exact reviewed modules; never execute product Python or index.ts."""
    parent = repo / SOURCE_DIR
    if any((repo / path).is_symlink() for path in (
        "supabase", "supabase/functions", str(SOURCE_DIR),
    )):
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_UNSAFE_PATH")
    if not parent.is_dir():
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_MODULES_MISSING")
    sources = {}
    for name, expected in REVIEWED_BLOBS.items():
        path = parent / name
        if path.is_symlink() or not path.is_file():
            raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_UNSAFE_PATH")
        data = path.read_bytes()
        if not 0 < len(data) <= 120_000 or _git_blob(data) != expected:
            raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_UNREVIEWED_MODULE")
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError:
            raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_INVALID_MODULE") from None
        source = source.replace(
            "'@imagemagick/magick-wasm'", "'./vendor/dist/index.js'",
        )
        # The standalone Deno test executable uses an npm URL. Only exported
        # test functions are needed; do not run its import.meta.main section.
        if name == "test.ts":
            source = source.split("if (import.meta.main) {", 1)[0]
        sources[name] = source
    return sources


def _run_reviewed_user_worker(repo: Path) -> None:
    root = repo / EDGE_ROOT
    binary = root / "selected-runtime/bin/edge-runtime"
    vendor = root / "selected-compat/user/vendor"
    for path in (root, binary, vendor):
        if path.is_symlink():
            raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_UNSAFE_TOOLS")
    if not (binary.is_file() and os.access(binary, os.X_OK)
            and vendor.is_dir()
            and (root / "local-smoke-pass").is_file()
            and (root / "edge-runtime-smoke-pass").is_file()):
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_PREPARE_MISSING")
    source = _reviewed_source(repo)
    workspace = root / "selected-reviewed-source"
    if workspace.exists() or workspace.is_symlink():
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_WORKSPACE_OCCUPIED")
    user = workspace / "user"
    main = workspace / "main"
    user.mkdir(parents=True, mode=0o700)
    main.mkdir(mode=0o700)
    # Use the already verified local npm cache; do not fetch or install anything.
    shutil.copytree(vendor, user / "vendor", symlinks=False)
    for name, text in source.items():
        (user / name).write_text(text, encoding="utf-8")
    (user / "index.ts").write_text(USER_MAIN, encoding="utf-8")
    (main / "index.ts").write_text(MAIN_SERVICE, encoding="utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root / "home"),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "DENO_DIR": str(root / "deno-cache"),
        "DENO_NO_UPDATE_CHECK": "1",
        "DENO_NO_PACKAGE_JSON": "1",
        "CLASSMO_SOURCE_USER_DIR": str(user),
    }
    try:
        process = subprocess.Popen(
            [str(binary), "start", "--ip", "127.0.0.1", "--port", str(port),
             "--main-service", str(main)],
            cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_SPAWN_FAILED") from None
    try:
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_EARLY_EXIT")
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                conn.request("GET", "/source")
                response = conn.getresponse()
                if response.status == 200 and response.read(200) == MARKER:
                    return
            except (OSError, http.client.HTTPException):
                pass
            finally:
                conn.close()
            time.sleep(0.3)
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_TEST_FAILED")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def prepare_reviewed_classmo_source(
    repo: Path, *, project: str, command: str, task: str | None,
    source_sha: str, repository: str, branch: str,
    reader: Callable[[str], object],
) -> bool:
    """Call ONLY inside the authenticated trusted parent before Codex is started.

    An unreviewed PR head fails closed; this is not a general new execution path.
    The selected runtime/codec preflight and PR guards run before this check.
    """
    if not needs_classmo_edge_preparation(project, command, task, source_sha):
        return False
    if source_sha != REVIEWED_SOURCE_HEAD:
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_HEAD_NOT_REVIEWED")
    verify_classmo_pr_identity(
        repo, repository=repository, branch=branch,
        source_sha=source_sha, reader=reader,
    )
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True, check=True, text=True, timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_GIT_UNAVAILABLE") from None
    if result.stdout:
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_WORKTREE_CHANGED")
    _run_reviewed_user_worker(repo)
    # No approval change, tracked product files or synthetic-provider mutations.
    after = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True, check=True, text=True, timeout=15,
    )
    if after.stdout:
        raise SelectedSourceRuntimeError("CLASSMO_SOURCE_RUNTIME_WORKTREE_CHANGED")
    verify_classmo_pr_identity(
        repo, repository=repository, branch=branch,
        source_sha=source_sha, reader=reader,
    )
    (repo / EDGE_ROOT / "reviewed-source-pass").write_text(
        f"source_head={source_sha}\nselected_edge_runtime=1.76.2\n"
        "synthetic_tests_only=true\nprovider=false\n",
        encoding="ascii",
    )
    return True
