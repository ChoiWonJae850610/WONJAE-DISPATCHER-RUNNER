"""Synthetic-only safety checks; do not download Deno or run any product source."""

import hashlib
import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from wonjae_dispatcher_runner import classmo_edge_preparation as edge
from wonjae_dispatcher_runner.classmo_edge_preparation import (
    EdgePrerequisiteError,
    edge_environment,
    needs_classmo_edge_preparation,
    prepare_classmo_edge,
)


def test_only_registered_classmo_141_retry_or_resume_is_eligible():
    for command in ("retry", "resume"):
        for head in (
            "e315b29fd22629cb9eedcacd17b355b6b2dab6be",
            "a" * 40,
        ):
            assert needs_classmo_edge_preparation(
                "CLASSMO", command, edge.SOURCE_TASK, head,
            )
    for project, command, task in (
        ("WAFL", "resume", edge.SOURCE_TASK),
        ("ESC", "retry", edge.SOURCE_TASK),
        ("MUVEL", "resume", edge.SOURCE_TASK),
        ("CLASSMO", "next", edge.SOURCE_TASK),
        ("CLASSMO", "resume", "CLASSMO-OTHER"),
        ("CLASSMO", "retry", "CLASSMO-OTHER"),
    ):
        assert not needs_classmo_edge_preparation(
            project, command, task, "e315b29fd22629cb9eedcacd17b355b6b2dab6be",
        )
    for bad_head in ("", "0" * 39, "not-a-commit"):
        with pytest.raises(EdgePrerequisiteError, match="EXACT_HEAD_INVALID"):
            needs_classmo_edge_preparation(
                "CLASSMO", "resume", edge.SOURCE_TASK, bad_head,
            )


def test_unrelated_products_cannot_fetch_or_modify_workspace(tmp_path, monkeypatch):
    def reject():
        raise AssertionError("public network cannot run for an unrelated project")

    monkeypatch.setattr(edge, "_public_deno_archive", reject)
    assert not prepare_classmo_edge(
        tmp_path, project="WAFL", command="resume", task=edge.SOURCE_TASK,
        source_sha="e315b29fd22629cb9eedcacd17b355b6b2dab6be",
    )
    assert not list(tmp_path.iterdir())


def test_pin_is_a_fixed_public_release_asset_not_an_unbounded_latest_channel():
    assert edge.DENO_URL == (
        "https://github.com/denoland/deno/releases/download/v2.9.7/"
        "deno-x86_64-unknown-linux-gnu.zip"
    )
    assert edge.MAGICK_VERSION == "0.0.44"
    assert edge.DENO_ZIP_SHA256 == (
        "c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490"
    )
    assert "MagickFormat.Jpeg" in edge._SMOKE
    assert "MagickFormat.WebP" in edge._SMOKE
    assert "MagickFormat.Png" in edge._SMOKE
    assert "npm:@imagemagick/magick-wasm@0.0.44" in edge._SMOKE


def test_invalid_public_archive_rejected_before_extraction(monkeypatch):
    with pytest.raises(EdgePrerequisiteError, match="digest mismatch"):
        edge._verify_deno_zip(b"invalid")
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w") as archive:
        archive.writestr("deno", b"\x7fELF" + b"\0" * 5_000_000)
    data = memory.getvalue()
    monkeypatch.setattr(edge, "DENO_ZIP_SHA256", hashlib.sha256(data).hexdigest())
    assert edge._verify_deno_zip(data).startswith(b"\x7fELF")
    with pytest.raises(EdgePrerequisiteError, match="digest mismatch"):
        edge._verify_deno_zip(data + b"tampered")


def _fixture(repo: Path):
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("node_modules/\n")
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "x@example.invalid"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Synthetic"],
                   check=True)
    (repo / "README.md").write_text("fixture\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)


def test_verified_offline_runtime_environment_only_after_local_smoke(tmp_path):
    repo = tmp_path / "repo"
    _fixture(repo)
    assert edge_environment(repo) == {}
    root = edge._safe_artifact_dir(repo)
    (root / "bin").mkdir(parents=True)
    runtime = root / "bin/deno"
    runtime.write_bytes(b"\x7fELF synthetic")
    runtime.chmod(0o700)
    (root / "deno-cache").mkdir()
    assert edge_environment(repo) == {}
    (root / "local-smoke-pass").write_text("local_only=true\n")
    env = edge_environment(repo)
    assert env["DENO_DIR"] == str(root / "deno-cache")
    assert env["DENO_NO_UPDATE_CHECK"] == "1"
    assert "TOKEN" not in "".join(env.keys())
    assert os.access(runtime, os.X_OK)
    subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                   capture_output=True, text=True, check=True)


def test_source_handoff_does_not_add_secrets_or_enable_network():
    src = (Path(__file__).resolve().parents[1]
           / "src/wonjae_dispatcher_runner/direct_worker.py").read_text()
    wrapper = (Path(__file__).resolve().parents[1]
               / "scripts/run_direct_worker_recovery.py").read_text()
    assert "edge_environment(repo_path)" in src
    assert "source_environment(codex_home" in src
    assert "network_access\": False" in src
    assert "prepare_classmo_edge(" in wrapper
    assert "reader=lambda endpoint: read(endpoint, token)" in wrapper
    assert wrapper.index("image_ready = prepare_classmo_edge(") < wrapper.index(
        "prepared = prepare_npm(repo, preparation)"
    )
    assert "NPM_CONFIG_OFFLINE" in (Path(__file__).resolve().parents[1]
                                  / "src/wonjae_dispatcher_runner/npm_preparation.py").read_text()


def test_public_deno_preflight_stage_codes_are_fixed_and_never_echo_stderr():
    adversarial = "Bearer SENSITIVE_FAKE_TOKEN\nfailed to fetch package from upstream"
    samples = (
        ("--version", "", adversarial, "CLASSMO_EDGE_DENO_BINARY_START_FAILED"),
        ("run", "", adversarial, "CLASSMO_EDGE_NPM_TRANSPORT_OR_TRUST_FAILED"),
        (
            "run", "", "Unexpected TypeScript import error: file:///arbitrary",
            "CLASSMO_EDGE_DENO_MODULE_OR_NPM_RESOLUTION_FAILED",
        ),
        (
            "run", "CLASSMO_EDGE_STAGE_JS_MODULE_IMPORTED\n", adversarial,
            "CLASSMO_EDGE_WASM_RESOURCE_OR_INIT_FAILED",
        ),
        (
            "run",
            "CLASSMO_EDGE_STAGE_JS_MODULE_IMPORTED\n"
            "CLASSMO_EDGE_STAGE_WASM_INITIALIZED\n", adversarial,
            "CLASSMO_EDGE_IMAGE_CODEC_SMOKE_FAILED",
        ),
    )
    for command, output, stderr, expected in samples:
        message = edge.safe_deno_failure_code(command, output, stderr)
        assert message == expected
        assert "SENSITIVE" not in message
        assert "Bearer" not in message
        assert "arbitrary" not in message


def test_deno_package_pin_and_host_ca_do_not_expand_codex_network_scope():
    script = (
        Path(__file__).resolve().parents[1]
        / "src/wonjae_dispatcher_runner/classmo_edge_preparation.py"
    ).read_text()
    assert '"DENO_TLS_CA_STORE": "system"' in script
    assert '"CLASSMO_EDGE_STAGE_JS_MODULE_IMPORTED"' in script
    assert '"CLASSMO_EDGE_STAGE_WASM_INITIALIZED"' in script
    assert '"--allow-net=registry.npmjs.org"' in script
    assert 'capture_output=True' in script
    assert 'safe_deno_failure_code(args[1], result.stdout, result.stderr)' in script
    assert 'print(result.stderr)' not in script
    assert 'print(result.stdout)' not in script
