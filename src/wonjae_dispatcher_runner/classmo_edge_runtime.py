"""Pinned, credential-free local Supabase Edge worker compatibility preflight.

Only the trusted CLASSMO #141 retry/resume parent calls this after the existing
exact-PR guard and three-format Deno smoke. Never deploy or touch a provider.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import zstandard

RUNTIME_VERSION = "1.76.2"  # SHA-256-pinned release asset tag, not the CLI version.
CLI_VERSION = "0.1.0"  # Upstream v1.76.2 cli/Cargo.toml package version.
ARCHIVE_NAME = "edge-runtime-v1.76.2-linux-amd64.tar.zst"
ARCHIVE_URL = (
    "https://github.com/supabase/slim-services/releases/download/"
    "edge-runtime-v1.76.2/" + ARCHIVE_NAME
)
ARCHIVE_SHA256 = "d3202f3350eda99dd76bbf7e5d2bc1801325898be1befffe581bd32f456e8417"
PASS_MARKER = b"CLASSMO_SELECTED_EDGE_RUNTIME_USER_CODEC_PASS"
_PACKAGE_ROOT = Path("npm/registry.npmjs.org/@imagemagick/magick-wasm/0.0.44")
_MAIN = """
const servicePath = Deno.env.get("CLASSMO_EDGE_USER_DIR");
if (!servicePath) throw Error("missing local test service");
Deno.serve(async (req) => {
  const worker = await EdgeRuntime.userWorkers.create({
    servicePath,
    memoryLimitMb: 150,
    workerTimeoutMs: 30000,
    noModuleCache: false,
    envVars: [],
    forceCreate: false,
    cpuTimeSoftLimitMs: 10000,
    cpuTimeHardLimitMs: 20000,
    staticPatterns: ["**/*.wasm"],
    context: { useReadSyncFileAPI: true },
  });
  return worker.fetch(req);
});
"""
_USER_PREFIX = """
import { ImageMagick, initializeImageMagick, MagickFormat } from
  "./vendor/dist/index.js";
const wasm = await Deno.readFile(
  new URL("./vendor/dist/x86/magick.wasm", import.meta.url)
);
await initializeImageMagick(wasm);
const png = Uint8Array.from(atob("__PNG__"), ch => ch.charCodeAt(0));
for (const format of [MagickFormat.Png, MagickFormat.Jpeg, MagickFormat.WebP]) {
  const encoded = ImageMagick.read(png, image =>
    image.write(format, bytes => Uint8Array.from(bytes)));
  if (!encoded.length) throw Error("empty codec output");
  ImageMagick.read(encoded, image => {
    if (image.width !== 1 || image.height !== 1) {
      throw Error("decoded dimensions mismatch");
    }
  });
}
Deno.serve(() => new Response("CLASSMO_SELECTED_EDGE_RUNTIME_USER_CODEC_PASS"));
"""


class SelectedEdgeRuntimeError(RuntimeError):
    """Sanitized failure that must block source handoff, without external logs."""


def verify_archive(data: bytes) -> None:
    if not data or len(data) > 40_000_000:
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_ARCHIVE_SIZE_REJECTED")
    if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_ARCHIVE_DIGEST_MISMATCH")


def public_runtime_archive() -> bytes:
    """Fetch only an exact public release asset, with no proxy or credentials."""
    opener = build_opener(ProxyHandler({}))
    request = Request(ARCHIVE_URL, headers={"User-Agent": "classmo-edge-local-runtime"})
    try:
        with opener.open(request, timeout=60) as response:
            data = response.read(40_000_001)
    except (HTTPError, URLError, TimeoutError, OSError):
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_PUBLIC_RELEASE_UNAVAILABLE") from None
    verify_archive(data)
    return data


def matches_cli_version(stdout: str) -> bool:
    """Check the official CLI first line; release identity comes from archive SHA-256."""
    return bool(re.match(r"\Aedge-runtime " + re.escape(CLI_VERSION) + r"(?:\r?\n|\Z)", stdout))


def _extract_runtime_archive(data: bytes, runtime_root: Path) -> None:
    """Decode the pinned tar.zst without assuming a host-installed zstd binary.

    Extract regular files and directories only, under a fresh private root.
    Do not trust tar member paths, links, device nodes, or archive permissions.
    """
    verify_archive(data)
    try:
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as members:
                total_bytes = 0
                seen = set()
                for index, member in enumerate(members, 1):
                    if index > 2_000:
                        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_ARCHIVE_UNSAFE")
                    name = member.name
                    while name.startswith("./"):
                        name = name[2:]
                    if name in ("", ".") and member.isdir():
                        continue
                    if member.isdir():
                        name = name.rstrip("/")
                    parts = name.split("/")
                    if (name.startswith("/") or "\\" in name or len(parts) > 32
                            or any(part in ("", ".", "..") for part in parts)
                            or name in seen or not (member.isdir() or member.isfile())):
                        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_ARCHIVE_UNSAFE")
                    seen.add(name)
                    target = runtime_root.joinpath(*parts)
                    if target.is_symlink():
                        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_ARCHIVE_UNSAFE")
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        target.chmod(0o700)
                        continue
                    total_bytes += member.size
                    if (member.size < 0 or member.size > 300_000_000
                            or total_bytes > 600_000_000):
                        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_ARCHIVE_UNSAFE")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = members.extractfile(member)
                    if source is None:
                        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_EXTRACT_FAILED")
                    with source, target.open("xb") as output:
                        remaining = member.size
                        while remaining:
                            chunk = source.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise SelectedEdgeRuntimeError(
                                    "CLASSMO_EDGE_RUNTIME_EXTRACT_FAILED"
                                )
                            output.write(chunk)
                            remaining -= len(chunk)
                    target.chmod(0o700 if member.mode & 0o111 else 0o600)
    except (OSError, ValueError, EOFError, tarfile.TarError, zstandard.ZstdError):
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_EXTRACT_FAILED") from None


def _install_runtime(root: Path, data: bytes) -> Path:
    """Extract an already SHA-256-pinned official native Linux release."""
    verify_archive(data)
    runtime_root = root / "selected-runtime"
    if runtime_root.exists() or runtime_root.is_symlink():
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_PATH_ALREADY_EXISTS")
    runtime_root.mkdir()
    _extract_runtime_archive(data, runtime_root)
    binary = runtime_root / "bin/edge-runtime"
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_NATIVE_BINARY_MISSING")
    try:
        version = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True,
            timeout=15, check=False, env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_BINARY_START_FAILED") from None
    if version.returncode != 0 or not matches_cli_version(version.stdout):
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_VERSION_MISMATCH")
    return binary


def _offline_codec_files(root: Path) -> Path:
    """Copy the previously tested npm cache into a local-only Edge user worker."""
    package = root / "deno-cache" / _PACKAGE_ROOT
    package = package.resolve(strict=False)
    expected_parent = (root / "deno-cache" / _PACKAGE_ROOT).parent.resolve()
    if package.parent != expected_parent or not package.is_dir() or package.is_symlink():
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_PINNED_NPM_CACHE_MISSING")
    manifest = package / "package.json"
    try:
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_NPM_MANIFEST_INVALID") from None
    if version != "0.0.44":
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_NPM_VERSION_MISMATCH")
    entries = list(package.rglob("*"))
    if len(entries) > 500 or any(p.is_symlink() for p in entries):
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_NPM_CACHE_UNSAFE")
    if sum(p.stat().st_size for p in entries if p.is_file()) > 50_000_000:
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_NPM_CACHE_OVERSIZED")
    for expected in ("dist/index.js", "dist/x86/magick.wasm"):
        if not (package / expected).is_file():
            raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_NPM_EXPORT_MISSING")
    user = root / "selected-compat" / "user"
    user.mkdir(parents=True, exist_ok=False)
    shutil.copytree(package, user / "vendor", symlinks=False)
    return user


def _edge_worker_smoke(binary: Path, root: Path, png_base64: str) -> None:
    """Require actual user-worker HTTP execution on loopback, never main-only PASS."""
    user = _offline_codec_files(root)
    main = root / "selected-compat" / "main"
    main.mkdir()
    (main / "index.ts").write_text(_MAIN, encoding="utf-8")
    (user / "index.ts").write_text(
        _USER_PREFIX.replace("__PNG__", png_base64), encoding="utf-8",
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root / "home"),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "DENO_DIR": str(root / "deno-cache"),
        "DENO_NO_UPDATE_CHECK": "1",
        "DENO_NO_PACKAGE_JSON": "1",
        "CLASSMO_EDGE_USER_DIR": str(user),
    }
    try:
        process = subprocess.Popen(
            [str(binary), "start", "--ip", "127.0.0.1", "--port", str(port),
             "--main-service", str(main)],
            env=env, cwd=root, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_SPAWN_FAILED") from None
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_EXITED_EARLY")
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.request("GET", "/codec")
                response = connection.getresponse()
                body = response.read(200)
                if response.status == 200 and body == PASS_MARKER:
                    return
            except (OSError, http.client.HTTPException):
                pass
            finally:
                connection.close()
            time.sleep(0.3)
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_USER_CODEC_SMOKE_FAILED")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def prepare_selected_edge_runtime(root: Path, png_base64: str) -> None:
    """Require exact archive, pinned local package and real Edge user worker PASS."""
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise SelectedEdgeRuntimeError("CLASSMO_EDGE_RUNTIME_PLATFORM_UNSUPPORTED")
    binary = _install_runtime(root, public_runtime_archive())
    _edge_worker_smoke(binary, root, png_base64)
    (root / "edge-runtime-smoke-pass").write_text(
        "selected_supabase_edge_runtime=1.76.2\n"
        "imagemagick_wasm=0.0.44\n"
        "local_user_worker=true\nprovider_or_deployment=false\n",
        encoding="ascii",
    )
