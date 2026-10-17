"""No-secret synthetic contract tests for one reviewed CLASSMO source runtime preflight."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wonjae_dispatcher_runner import classmo_source_runtime as selected
from wonjae_dispatcher_runner.classmo_edge_preparation import EDGE_ROOT, SOURCE_TASK


def _prepared_repo(root: Path, monkeypatch) -> Path:
    repo = root / "product"
    source = repo / selected.SOURCE_DIR
    source.mkdir(parents=True)
    mock = {
        "normalize.ts": (
            "import { ImageMagick } from '@imagemagick/magick-wasm';\n"
            "export const valid = true;\n"
        ),
        "test.ts": (
            "export async function runTests() { return true; }\n"
            "if (import.meta.main) { throw Error('not a trusted import'); }\n"
        ),
        "boundary-test.ts": "export const checked = true;\n",
        "boundary.ts": "export const boundary = true;\n",
        "gateway.ts": "export const gateway = true;\n",
    }
    for name, text in mock.items():
        (source / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(
        selected, "REVIEWED_BLOBS",
        {name: selected._git_blob(text.encode()) for name, text in mock.items()},
    )
    artifacts = repo / EDGE_ROOT
    (artifacts / "selected-runtime/bin").mkdir(parents=True)
    binary = artifacts / "selected-runtime/bin/edge-runtime"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    vendor = artifacts / "selected-compat/user/vendor/dist/x86"
    vendor.mkdir(parents=True)
    (vendor / "magick.wasm").write_bytes(b"synthetic")
    (vendor.parent / "index.js").write_text("export const synthetic = true;\n")
    (artifacts / "local-smoke-pass").write_text("local_only=true\n")
    (artifacts / "edge-runtime-smoke-pass").write_text("selected=1.76.2\n")
    (artifacts / "home").mkdir()
    (artifacts / "deno-cache").mkdir()
    return repo


def test_only_existing_reviewed_classmo_head_can_enter_trusted_source_check(tmp_path):
    calls = []

    def reader(_):
        calls.append("network")
        raise AssertionError("must not reach GitHub")

    base = {
        "repo": tmp_path, "project": "CLASSMO", "command": "retry",
        "task": SOURCE_TASK, "source_sha": selected.REVIEWED_SOURCE_HEAD,
        "repository": "ChoiWonJae850610/CLASSMO", "branch": "cloud-dev-v1",
        "reader": reader,
    }
    assert selected.prepare_reviewed_classmo_source(
        **{**base, "project": "ESC"},
    ) is False
    assert selected.prepare_reviewed_classmo_source(
        **{**base, "command": "next"},
    ) is False
    assert selected.prepare_reviewed_classmo_source(
        **{**base, "task": "WRONG_TASK"},
    ) is False
    with pytest.raises(selected.SelectedSourceRuntimeError, match="HEAD_NOT_REVIEWED"):
        selected.prepare_reviewed_classmo_source(
            **{**base, "source_sha": "f" * 40},
        )
    assert not calls


def test_reviewed_source_is_pinned_and_product_python_never_executes(
    tmp_path, monkeypatch,
):
    repo = _prepared_repo(tmp_path, monkeypatch)
    modules = selected._reviewed_source(repo)
    assert "./vendor/dist/index.js" in modules["normalize.ts"]
    assert "'@imagemagick/magick-wasm'" not in modules["normalize.ts"]
    assert "import.meta.main" not in modules["test.ts"]
    assert set(modules) == set(selected.REVIEWED_BLOBS)

    src = repo / selected.SOURCE_DIR / "normalize.ts"
    src.write_text(src.read_text() + "// tampered\n")
    with pytest.raises(selected.SelectedSourceRuntimeError, match="UNREVIEWED_MODULE"):
        selected._reviewed_source(repo)
    src.unlink()
    src.symlink_to(repo / selected.SOURCE_DIR / "gateway.ts")
    with pytest.raises(selected.SelectedSourceRuntimeError, match="UNSAFE_PATH"):
        selected._reviewed_source(repo)


def test_trusted_loopback_worker_scrubs_all_secrets_and_requires_marker(
    tmp_path, monkeypatch,
):
    repo = _prepared_repo(tmp_path, monkeypatch)
    events = []

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            events.append("terminated")

        def wait(self, timeout):
            assert timeout == 5
            return 0

    class FakeResponse:
        status = 200

        def read(self, size):
            assert size == 200
            return selected.MARKER

    class FakeConnection:
        def __init__(self, host, port, timeout):
            assert host == "127.0.0.1"
            assert isinstance(port, int) and port > 0
            assert timeout == 3

        def request(self, method, path):
            assert (method, path) == ("GET", "/source")

        def getresponse(self):
            return FakeResponse()

        def close(self):
            events.append("closed")

    def spawn(args, *, cwd, env, stdout, stderr):
        assert args[1:4] == ["start", "--ip", "127.0.0.1"]
        assert "--main-service" in args
        assert cwd == repo / EDGE_ROOT
        assert env == {
            "PATH": "/usr/bin:/bin",
            "HOME": str(repo / EDGE_ROOT / "home"),
            "LANG": "C.UTF-8",
            "TZ": "UTC",
            "DENO_DIR": str(repo / EDGE_ROOT / "deno-cache"),
            "DENO_NO_UPDATE_CHECK": "1",
            "DENO_NO_PACKAGE_JSON": "1",
            "CLASSMO_SOURCE_USER_DIR": str(repo / EDGE_ROOT / "selected-reviewed-source/user"),
        }
        assert stdout == stderr == selected.subprocess.DEVNULL
        events.append("spawned")
        return FakeProcess()

    monkeypatch.setattr(selected.subprocess, "Popen", spawn)
    monkeypatch.setattr(selected.http.client, "HTTPConnection", FakeConnection)
    selected._run_reviewed_user_worker(repo)
    user = repo / EDGE_ROOT / "selected-reviewed-source/user"
    assert (user / "vendor/dist/x86/magick.wasm").read_bytes() == b"synthetic"
    assert "CLASSMO_NORMALIZATION_SYNTHETIC_PASS" in (user / "index.ts").read_text()
    assert "CLASSMO_BOUNDARY_SYNTHETIC_PASS" in (user / "index.ts").read_text()
    assert "import.meta.main" not in (user / "test.ts").read_text()
    assert events == ["spawned", "closed", "terminated"]


def test_missing_preflight_or_early_exit_is_not_pass(tmp_path, monkeypatch):
    repo = _prepared_repo(tmp_path, monkeypatch)
    missing = repo / EDGE_ROOT / "edge-runtime-smoke-pass"
    missing.unlink()
    with pytest.raises(selected.SelectedSourceRuntimeError, match="PREPARE_MISSING"):
        selected._run_reviewed_user_worker(repo)
    missing.write_text("selected=1.76.2\n")
    monkeypatch.setattr(
        selected.subprocess, "Popen",
        lambda *args, **kwargs: SimpleNamespace(
            poll=lambda: 1, terminate=lambda: None, wait=lambda timeout: 1,
        ),
    )
    with pytest.raises(selected.SelectedSourceRuntimeError, match="EARLY_EXIT"):
        selected._run_reviewed_user_worker(repo)


def test_parent_orders_source_gate_after_pinned_runtime_and_before_codex():
    root = Path(__file__).resolve().parents[1]
    wrapper = (root / "scripts/run_direct_worker_recovery.py").read_text()
    assert wrapper.index("image_ready = prepare_classmo_edge(") < wrapper.index(
        "source_pass = prepare_reviewed_classmo_source("
    ) < wrapper.index("child_env = {key:")
    assert wrapper.index('child_env["CLASSMO_TRUSTED_SOURCE_RUNTIME_HEAD"]') < (
        wrapper.index('Path(__file__).with_name("run_direct_worker.py")')
    )
    assert "exec(compile(" not in wrapper
    assert "check-selected-runtime.py" not in wrapper
    assert '"PRODUCT_TOKEN", "RUNNER_TOKEN"' in wrapper
    engine = (root / "src/wonjae_dispatcher_runner/direct_worker.py").read_text()
    assert '"reviewed-source-pass"' in engine
    assert "trusted_source_runtime_pass" in engine
    assert "Do not run check-selected-runtime.py" in engine
    assert "network_access" not in selected.MAIN_SERVICE
    assert "https://" not in selected.USER_MAIN


def test_trusted_gate_persists_exact_sha_evidence_only(tmp_path, monkeypatch):
    repo = _prepared_repo(tmp_path, monkeypatch)
    mock_calls = []
    monkeypatch.setattr(
        selected, "verify_classmo_pr_identity",
        lambda *args, **kwargs: mock_calls.append(kwargs["source_sha"]),
    )
    monkeypatch.setattr(
        selected, "_run_reviewed_user_worker",
        lambda arg: mock_calls.append("source") if arg == repo else None,
    )
    monkeypatch.setattr(
        selected.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )
    assert selected.prepare_reviewed_classmo_source(
        repo, project="CLASSMO", command="resume",
        task=SOURCE_TASK, source_sha=selected.REVIEWED_SOURCE_HEAD,
        repository="ChoiWonJae850610/CLASSMO", branch="cloud-dev-v1",
        reader=lambda _: None,
    ) is True
    marker = (repo / EDGE_ROOT / "reviewed-source-pass").read_text()
    assert marker == (
        f"source_head={selected.REVIEWED_SOURCE_HEAD}\n"
        "selected_edge_runtime=1.76.2\n"
        "synthetic_tests_only=true\nprovider=false\n"
    )
    assert mock_calls == [
        selected.REVIEWED_SOURCE_HEAD, "source", selected.REVIEWED_SOURCE_HEAD,
    ]
    assert "secret" not in marker.lower()
    assert "token" not in json.dumps({"marker": marker}).lower()
