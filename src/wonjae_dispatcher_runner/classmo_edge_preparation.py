"""Credential-free exact CLASSMO #141 Edge image-tool preparation.

Runs in the trusted parent only after authenticated Owner retry/resume and
fresh exact existing-PR identity checks. Leaves tracked product files unchanged.
The local Deno/WASM smoke is not live Supabase Edge-runtime PASS.
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
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

EDGE_ROOT = Path("node_modules/.direct-worker/classmo-edge-wasm")
SOURCE_REPO = "ChoiWonJae850610/CLASSMO"
SOURCE_BRANCH = "cloud-dev-v1"
SOURCE_BASE = "6df7d54401bca708e1089fe9a0b806cff2e0ef50"
PR_NUMBER = 141
PR_BRANCH = "direct/CLASSMO-38828604168"
PR_RUN_ID = 38828604168
PR_DOC = "docs/ux/CLASSMO_EDITORIAL_MEDIA_027_P2_BACKEND_20261015.md"
_SHA = re.compile(r"[a-f0-9]{40}\Z")
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
console.log("CLASSMO_EDGE_STAGE_JS_MODULE_IMPORTED");
const wasm = await Deno.readFile(
  new URL("magick.wasm",
    import.meta.resolve("npm:@imagemagick/magick-wasm@0.0.44"))
);
await initializeImageMagick(wasm);
console.log("CLASSMO_EDGE_STAGE_WASM_INITIALIZED");
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


def safe_deno_failure_code(command: str, stdout: str, stderr: str) -> str:
    """Return a fixed, credential-free stage code; never surface raw subprocess logs.

    The first JS marker is emitted only after Deno resolves the pinned public
    package. The second marker proves the WebAssembly module initialized. A
    failed CLI or codec step must never be classified as a successful source.
    """
    if command == "--version":
        return "CLASSMO_EDGE_DENO_BINARY_START_FAILED"
    if "CLASSMO_EDGE_STAGE_JS_MODULE_IMPORTED" not in stdout:
        if any(word in stderr.lower() for word in (
            "certificate", "tls", "dns", "network", "connection", "proxy",
            "failed to fetch", "error sending request",
        )):
            return "CLASSMO_EDGE_NPM_TRANSPORT_OR_TRUST_FAILED"
        return "CLASSMO_EDGE_DENO_MODULE_OR_NPM_RESOLUTION_FAILED"
    if "CLASSMO_EDGE_STAGE_WASM_INITIALIZED" not in stdout:
        return "CLASSMO_EDGE_WASM_RESOURCE_OR_INIT_FAILED"
    return "CLASSMO_EDGE_IMAGE_CODEC_SMOKE_FAILED"


def needs_classmo_edge_preparation(
    project: str, command: str, task: str | None, source_sha: str,
) -> bool:
    # Neither an unrelated project nor a different CLASSMO source task can
    # trigger public tool downloads. Retry and resume of THIS task both need
    # the same public toolchain; the source HEAD is checked live, not pinned to
    # the initial document-only PR commit.
    if project != "CLASSMO" or command not in ("retry", "resume") or task != SOURCE_TASK:
        return False
    if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
        raise EdgePrerequisiteError("CLASSMO_EDGE_EXACT_HEAD_INVALID")
    return True


def _obj(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _read(reader: Callable[[str], object], endpoint: str) -> object:
    try:
        return reader(endpoint)
    except Exception:
        # GitHub CLI can include private transport context in its stderr.
        # Never expose it through a traceback or public Actions summary.
        raise EdgePrerequisiteError("CLASSMO_EDGE_PR_READ_UNAVAILABLE") from None


def _allowed_pr_file(filename: object, status: object) -> bool:
    if not isinstance(filename, str) or status not in ("added", "modified"):
        return False
    if filename in {
        PR_DOC,
        "scripts/validate-repository.mjs",
        "supabase/tests/editorial_media_p2_plan.json",
    }:
        return True
    return any(filename.startswith(prefix) and len(filename) > len(prefix)
               for prefix in (
                   "supabase/functions/editorial-media-prepare/",
                   "packages/domain/src/media/",
                   "packages/domain/test/media",
                   "packages/domain/tests/media",
                   "tests/media",
               ))


def verify_classmo_pr_identity(
    repo: Path, *, repository: str, branch: str,
    source_sha: str, reader: Callable[[str], object],
) -> None:
    """Prove existing sole authorized source PR identity from fresh GitHub GETs.

    This is a trusted-parent read gate, never a generic PR writer. It also
    applies if the same Runner legitimately appended a document-only commit.
    The original base/task/PR branch stay immutable; live PR head may advance.
    """
    if repository != SOURCE_REPO or branch != SOURCE_BRANCH:
        raise EdgePrerequisiteError("CLASSMO_EDGE_REGISTERED_ROUTE_MISMATCH")
    if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
        raise EdgePrerequisiteError("CLASSMO_EDGE_EXACT_HEAD_INVALID")
    try:
        origin = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        checkout = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        ancestor = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor",
             SOURCE_BASE, source_sha],
            check=False, capture_output=True, timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise EdgePrerequisiteError("CLASSMO_EDGE_LOCAL_GIT_IDENTITY_UNAVAILABLE") from None
    if origin != "https://github.com/" + SOURCE_REPO + ".git" or (
        checkout != source_sha or ancestor.returncode != 0
    ):
        raise EdgePrerequisiteError("CLASSMO_EDGE_LOCAL_SOURCE_OR_ANCESTRY_CHANGED")
    root = "repos/" + SOURCE_REPO + "/"
    current = _obj(_read(reader, root + "branches/" + SOURCE_BRANCH))
    if _obj(current.get("commit")).get("sha") != SOURCE_BASE:
        raise EdgePrerequisiteError("CLASSMO_EDGE_BASE_SHA_CHANGED")
    open_prs = _read(
        reader, root + "pulls?state=open&base=" + SOURCE_BRANCH + "&per_page=100",
    )
    if (not isinstance(open_prs, list) or len(open_prs) != 1
            or _obj(open_prs[0]).get("number") != PR_NUMBER):
        raise EdgePrerequisiteError("CLASSMO_EDGE_SINGLE_WRITER_PR_CHANGED")
    pr = _obj(_read(reader, root + "pulls/" + str(PR_NUMBER)))
    head = _obj(pr.get("head"))
    base = _obj(pr.get("base"))
    body = pr.get("body")
    if not (
        pr.get("number") == PR_NUMBER
        and pr.get("state") == "open"
        and pr.get("merged") is False
        and pr.get("draft") is False
        and pr.get("html_url") == "https://github.com/" + SOURCE_REPO
        + "/pull/" + str(PR_NUMBER)
        and head.get("sha") == source_sha
        and head.get("ref") == PR_BRANCH
        and _obj(head.get("repo")).get("full_name") == SOURCE_REPO
        and base.get("sha") == SOURCE_BASE
        and base.get("ref") == SOURCE_BRANCH
        and _obj(base.get("repo")).get("full_name") == SOURCE_REPO
        and isinstance(body, str)
        and "- project: `CLASSMO`" in body
        and "- command: `next`" in body
        and "- source_base_sha: `" + SOURCE_BASE + "`" in body
        and "- runner_run_id: `" + str(PR_RUN_ID) + "`" in body
    ):
        raise EdgePrerequisiteError("CLASSMO_EDGE_PR_IDENTITY_OR_HEAD_CHANGED")
    files = _read(reader, root + "pulls/" + str(PR_NUMBER) + "/files?per_page=100")
    if (not isinstance(files, list) or not 1 <= len(files) < 100
            or pr.get("changed_files") != len(files)
            or not all(_allowed_pr_file(_obj(x).get("filename"),
                                        _obj(x).get("status")) for x in files)):
        raise EdgePrerequisiteError("CLASSMO_EDGE_PR_CHANGED_PATHS_OUT_OF_SCOPE")
    ref = _obj(_read(reader, root + "git/ref/heads/" + PR_BRANCH))
    if _obj(ref.get("object")).get("sha") != source_sha:
        raise EdgePrerequisiteError("CLASSMO_EDGE_PR_REF_MOVED")



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
    repository: str = "", branch: str = "",
    reader: Callable[[str], object] | None = None,
) -> bool:
    """Pin public artifacts and smoke-test Deno/WASM before source handoff."""
    if not needs_classmo_edge_preparation(project, command, task, source_sha):
        return False
    if reader is None:
        raise EdgePrerequisiteError("CLASSMO_EDGE_PR_READER_UNAVAILABLE")
    # Independent remote PR identity must be established BEFORE any download,
    # local executable, npm cache or model source action can start.
    verify_classmo_pr_identity(
        repo, repository=repository, branch=branch, source_sha=source_sha,
        reader=reader,
    )
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise EdgePrerequisiteError("pinned Deno archive requires Linux x86_64")
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
        # Align trusted Deno public npm TLS with the verified host CA store;
        # no token, proxy secret, broader permission or network-capable Codex.
        "DENO_TLS_CA_STORE": "system",
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
            phase = "DENO_BINARY" if args[1] == "--version" else "DENO_PUBLIC_WASM"
            raise EdgePrerequisiteError(
                "CLASSMO_EDGE_" + phase + "_PROCESS_UNAVAILABLE"
            ) from exc
        if result.returncode != 0:
            raise EdgePrerequisiteError(
                safe_deno_failure_code(args[1], result.stdout, result.stderr)
            )
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
    # A writer could have moved the PR during npm download or WASM smoke.
    # Check the live PR/base/ref again before releasing tools to source Codex.
    verify_classmo_pr_identity(
        repo, repository=repository, branch=branch, source_sha=source_sha,
        reader=reader,
    )
    print("CLASSMO_EDGE_WASM_LOCAL_PREFLIGHT_PASS_NOT_EDGE_PROVIDER")
    return True
