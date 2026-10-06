"""No-provider smoke using the actual pinned Codex Linux sandbox, not a model.

Never accepts product tokens/auth or edits a registered product. Nonzero means
the candidate host/profile is NOT ready. Prints only assertion names/results.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from codex_cli_bin import bundled_codex_path

from wonjae_dispatcher_runner.home_isolation import (
    cli_overrides,
    protected_mount_placeholders,
    require_linux_host,
    source_environment,
)


def main() -> int:
    require_linux_host()
    subprocess.run(["unshare", "-Urn", "true"], check=True, timeout=15)
    if not shutil.which("bwrap"):
        raise RuntimeError("bubblewrap must be provisioned before smoke")
    codex = str(bundled_codex_path())
    with tempfile.TemporaryDirectory(prefix="direct-smoke-") as temporary:
        root = Path(temporary)
        product = root / "product"
        product.mkdir()
        subprocess.run(["git", "init", "-q", str(product)], check=True)
        for name in ("auth", "publication", "other-product", "cache", "trusted"):
            folder = root / name
            folder.mkdir()
            (folder / "sentinel").write_text("synthetic-no-secret\n")
        (product / "escape").symlink_to(root / "other-product", target_is_directory=True)
        (product / ".wonjae").mkdir()
        (product / ".wonjae" / "execution-state.yaml").write_text("protected\n")
        auth = root / "auth"
        (auth / "auth.json").write_text('{"synthetic": "no-secret"}\n')
        shell_home = Path("/nonexistent")
        env = source_environment(auth, shell_home)
        # These must be absent inside the actual sandbox, regardless of parent secrets.
        script = r'''
set -eu
test -z "${CODEX_AUTH_JSON:-}${GH_TOKEN:-}${GITHUB_TOKEN:-}${PRODUCT_TOKEN:-}"
test -z "${RUNNER_TOKEN:-}${AWS_SECRET_ACCESS_KEY:-}"
printf 'environment: PASS\n'
for folder in auth publication other-product cache trusted; do
  if cat "$1/$folder/sentinel" >/dev/null 2>&1; then exit 21; fi
done
if cat "$1/auth/auth.json" >/dev/null 2>&1; then exit 30; fi
if cat escape/sentinel >/dev/null 2>&1; then exit 22; fi
if cat /proc/1/root"$1/auth/sentinel" >/dev/null 2>&1; then exit 23; fi
printf 'host-read-and-symlink: PASS\n'
if touch ../other-product/changed 2>/dev/null; then exit 24; fi
if touch .git/changed 2>/dev/null; then exit 25; fi
if touch .wonjae/changed 2>/dev/null; then exit 26; fi
if touch AGENTS.md 2>/dev/null; then exit 31; fi
if touch .gitmodules 2>/dev/null; then exit 32; fi
if touch .github/changed 2>/dev/null; then exit 33; fi
if touch "$1-outside" 2>/dev/null; then exit 28; fi
printf 'protected-and-cross-project-write: PASS\n'
printf 'source\n' > allowed.txt
printf 'source-write: PASS\n'
if bash -c 'exec 3<>/dev/tcp/1.1.1.1/443' 2>/dev/null; then exit 27; fi
source_network_namespace="$(readlink /proc/self/ns/net)"
test -n "$source_network_namespace"
test "$source_network_namespace" != "$2"
printf 'network: PASS\n'
'''
        args = [codex]
        for override in cli_overrides(product):
            args += ["--config", override]
        args += ["sandbox", "--", "/bin/bash", "-c", script, "smoke", str(root),
                 os.readlink("/proc/self/ns/net")]
        with protected_mount_placeholders(product):
            result = subprocess.run(args, cwd=product, env=env, capture_output=True,
                                    text=True, timeout=45)
        expected = (
            "environment: PASS", "host-read-and-symlink: PASS",
            "protected-and-cross-project-write: PASS", "source-write: PASS", "network: PASS",
        )
        if result.returncode or any(line not in result.stdout for line in expected):
            # Synthetic smoke has no auth/source/private content; bounded diagnostics only.
            print(result.stderr[-3000:])
            raise RuntimeError(f"actual Codex Linux sandbox smoke failed: {result.returncode}")
        print(result.stdout.strip())
        files = subprocess.run(
            ["git", "-C", str(product), "ls-files", "--others", "--exclude-standard", "-z"],
            check=True, capture_output=True, text=True,
        ).stdout
        if set(filter(None, files.split("\x00"))) != {
            ".wonjae/execution-state.yaml", "allowed.txt", "escape",
        }:
            raise RuntimeError("sandbox introduced unexpected checkout files")
        print("trusted-placeholder-cleanup: PASS")
    print("provider/model/publication: NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
