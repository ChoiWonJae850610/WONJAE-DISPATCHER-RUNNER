"""Credential-free exact CLASSMO #141 Edge image-tool preparation.

Runs in the trusted parent only after an authenticated Owner resume. Leaves
tracked product files unchanged. The local Deno/WASM smoke is not live Edge PASS.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import subprocess
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

EDGE_ROOT = Path("node_modules/.direct-worker/classmo-edge-wasm")
SOURCE_HEAD = "80760bfaf9d62ab3c1697a66b9f9879833bb0a41"
SOURCE_TASK = "CLASSMO-EDITORIAL-MEDIA-P2-BACKEND-002"
DENO_VERSION = "2.9.7"
MAGICK_VERSION = "0.0.44"
DENO_ARCHIVE = "deno-x86_64-unknown-linux-gnu.zip"
DENO_URL = (
    "https://github.com/denoland/deno/releases/download/v"
    + DENO_VERSION + "/" + DENO_ARCHIVE
)
DENO_ZIP_SHA256 = "c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490"
_PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)
_SMOKE = """
import { ImageMagick, initializeImageMagick, MagickFormat } from
  "npm:@imagemagick/magick-wasm@0.0.44";
const wasm = await Deno.readFile(
  new URL("magick.wasm",
    import.meta.resolve("npm:@imagemagick/magick-wasm@0.0.44"))
);
await initializeImageMagick(wasm);
const png = Uint8Array.from(atob("__PNG__"), ch => ch.charCodeAt(0));
for (const format of [MagickFormat.Png, MagickFormat.Jpeg, MagickFormat.WebP]) {
  const converted = ImageMagick.read(png, image =>
    image.write(format, bytes => Uint8Array.from(bytes)));
  if (!converted.length) throw new Error("Codec re-encode produced empty bytes");
  ImageMagick.read(converted, image => {
    if (image.width !== 1 || image.height !== 1)
      throw new Error("Codec decode dimensions do not match");
  });
}
console.log("CLASSMO_EDGE_WASM_3_FORMATS_LOCAL_DENO_PASS_NOT_EDGE_RUNTIME");
""".replace("__PNG__", _PNG_1PX)


class EdgePrerequisiteError(RuntimeError):
    """Unverified public image tools; block before running any Codex source turn."""


def needs_classmo_edge_preparation(
    project: str, command: str, task: str | None, source_sha: str,
) -> bool:
    if project != "CLASSMO" or command != "resume" or task != SOURCE_TASK:
        return False
    if source_sha != SOURCE_HEAD:
        raise EdgePrerequisiteError("CLASSMO #141 exact source HEAD changed")
    return True


def _safe_artifact_dir(repo: Path) -> Path:
    if repo.is_symlink() or not (repo / ".git").is_dir():
        raise EdgePrerequisiteError("product checkout is not a regular Git directory")
    target = repo / EDGE_ROOT
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise EdgePrerequisiteError("Edge tools path is symlinked")
    if target.exists():
        raise EdgePrerequisiteError("Edge tools cannot overwrite an existing path")
    checked = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--quiet", str(EDGE_ROOT / "bin/deno")],
        timeout=15, check=False,
    )
    if checked.returncode != 0:
        raise EdgePrerequisiteError("product must ignore prepared Edge tools")
    return target


def _verify_deno_zip(data: bytes) -> bytes:
    if hashlib.sha256(data).hexdigest() != DENO_ZIP_SHA256:
        raise EdgePrerequisiteError("pinned Deno archive digest mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member = archive.getinfo("deno")
            is_link = ((member.external_attr >> 16) & 0o170000) == 0o120000
            if (member.is_dir() or is_link or member.file_size < 5_000_000
                    or member.file_size > 180_000_000):
                raise EdgePrerequisiteError("untrusted Deno ZIP member")
            binary = archive.read(member)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise EdgePrerequisiteError("pinned Deno ZIP unreadable") from exc
    if not binary.startswith(b"\x7fELF"):
        raise EdgePrerequisiteError("Deno ZIP did not contain a Linux executable")
    return binary


def _public_deno_archive() -> bytes:
    # No token/proxy credentials inherited. Exact SHA binds GitHub redirects.
    opener = build_opener(ProxyHandler({}))
    request = Request(DENO_URL, headers={"User-Agent": "trusted-classmo-edge-preflight"})
    try:
        with opener.open(request, timeout=45) as response:
            data = response.read(65_000_001)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise EdgePrerequisiteError("pinned public Deno release unavailable") from exc
    if len(data) > 65_000_000:
        raise EdgePrerequisiteError("Deno archive exceeds configured size")
    return data


def edge_environment(repo: Path) -> dict[str, str]:
    root = repo / EDGE_ROOT
    binary = root / "bin/deno"
    cache = root / "deno-cache"
    if not (binary.is_file() and os.access(binary, os.X_OK)
            and cache.is_dir() and (root / "local-smoke-pass").is_file()):
        return {}
    if any(x.is_symlink() for x in (root, binary, cache)):
        raise EdgePrerequisiteError("prepared Edge tools are symlinked")
    return {"DENO_DIR": str(cache), "DENO_NO_UPDATE_CHECK": "1"}


def prepare_classmo_edge(
    repo: Path, *, project: str, command: str,
    task: str | None, source_sha: str,
) -> bool:
    """Pin public artifacts and smoke-test Deno/WASM before source handoff."""
    if not needs_classmo_edge_preparation(project, command, task, source_sha):
        return False
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise EdgePrerequisiteError("pinned Deno archive requires Linux x86_64")
    current = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, timeout=15,
    ).strip()
    if current != SOURCE_HEAD:
        raise EdgePrerequisiteError("CLASSMO #141 checkout HEAD differs")
    root = _safe_artifact_dir(repo)
    binary = _verify_deno_zip(_public_deno_archive())
    (root / "bin").mkdir(parents=True, exist_ok=False)
    runtime = root / "bin/deno"
    runtime.write_bytes(binary)
    runtime.chmod(0o755)
    (root / "deno-cache").mkdir()
    (root / "home").mkdir()
    smoke = root / "edge-smoke.ts"
    smoke.write_text(_SMOKE, encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root / "home"),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "DENO_DIR": str(root / "deno-cache"),
        "DENO_NO_UPDATE_CHECK": "1",
        "DENO_NO_PACKAGE_JSON": "1",
    }
    invocations = (
        [str(runtime), "--version"],
        [str(runtime), "run", "--no-prompt", "--allow-read=" + str(root),
         "--allow-net=registry.npmjs.org", str(smoke)],
    )
    for args in invocations:
        try:
            result = subprocess.run(
                args, env=env, cwd=repo, check=False,
                capture_output=True, text=True, timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EdgePrerequisiteError("local Deno/WASM smoke unavailable") from exc
        if result.returncode != 0:
            raise EdgePrerequisiteError("Deno/WASM prerequisite did not pass")
        if args[1] == "--version" and not re.search(
            rf"^deno {re.escape(DENO_VERSION)}\b", result.stdout, re.MULTILINE,
        ):
            raise EdgePrerequisiteError("pinned Deno version mismatch")
        if args[1] == "run" and (
            "CLASSMO_EDGE_WASM_3_FORMATS_LOCAL_DENO_PASS_NOT_EDGE_RUNTIME"
            not in result.stdout
        ):
            raise EdgePrerequisiteError("WASM 3-format smoke not observed")
    (root / "local-smoke-pass").write_text(
        f"deno={DENO_VERSION}\nmagick={MAGICK_VERSION}\nlocal_only=true\n",
        encoding="ascii",
    )
    if subprocess.check_output(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"],
        text=True, timeout=15,
    ):
        raise EdgePrerequisiteError("preflight modified tracked product source")
    edge_environment(repo)
    print("CLASSMO_EDGE_WASM_LOCAL_PREFLIGHT_PASS_NOT_EDGE_PROVIDER")
    return True
