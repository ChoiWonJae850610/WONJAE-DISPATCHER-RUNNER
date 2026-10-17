"""Bounded, synthetic native Supabase Edge preflight tests; no live download or product run."""

import hashlib
import json
from pathlib import Path

import pytest

from wonjae_dispatcher_runner import classmo_edge_runtime as selected
from wonjae_dispatcher_runner import classmo_edge_preparation as edge


def test_native_pin_matches_a_published_fixed_linux_release():
    assert selected.RUNTIME_VERSION == "1.76.2"
    assert selected.ARCHIVE_NAME == "edge-runtime-v1.76.2-linux-amd64.tar.zst"
    assert selected.ARCHIVE_URL == (
        "https://github.com/supabase/slim-services/releases/download/"
        "edge-runtime-v1.76.2/edge-runtime-v1.76.2-linux-amd64.tar.zst"
    )
    assert selected.ARCHIVE_SHA256 == (
        "d3202f3350eda99dd76bbf7e5d2bc1801325898be1befffe581bd32f456e8417"
    )
    assert selected.PASS_MARKER == b"CLASSMO_SELECTED_EDGE_RUNTIME_USER_CODEC_PASS"
    assert "EdgeRuntime.userWorkers.create" in selected._MAIN
    assert "MagickFormat.WebP" in selected._USER_PREFIX
    assert "MagickFormat.Jpeg" in selected._USER_PREFIX
    assert "MagickFormat.Png" in selected._USER_PREFIX
    assert 'from\n  "./vendor/dist/index.js"' in selected._USER_PREFIX
    assert "npm:" not in selected._USER_PREFIX
    assert "https://" not in selected._USER_PREFIX


def test_bad_archive_rejected_prior_to_any_execution(monkeypatch):
    with pytest.raises(selected.SelectedEdgeRuntimeError, match="DIGEST_MISMATCH"):
        selected.verify_archive(b"bad release")
    with pytest.raises(selected.SelectedEdgeRuntimeError, match="SIZE_REJECTED"):
        selected.verify_archive(b"")
    with pytest.raises(selected.SelectedEdgeRuntimeError, match="SIZE_REJECTED"):
        selected.verify_archive(b"x" * 40_000_001)
    data = b"synthetic-trusted-release"
    monkeypatch.setattr(selected, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest())
    selected.verify_archive(data)
    with pytest.raises(selected.SelectedEdgeRuntimeError, match="DIGEST_MISMATCH"):
        selected.verify_archive(data + b"tampering")


def _cache(root: Path):
    package = root / "deno-cache" / selected._PACKAGE_ROOT
    (package / "dist/x86").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": "0.0.44"}))
    (package / "dist/index.js").write_text("export const genuine = true;\n")
    (package / "dist/x86/magick.wasm").write_bytes(b"synthetic WASM bytes")
    return package


def test_offline_package_is_exact_and_local(tmp_path):
    package = _cache(tmp_path)
    user = selected._offline_codec_files(tmp_path)
    assert user == tmp_path / "selected-compat/user"
    assert (user / "vendor/dist/index.js").read_bytes() == (
        package / "dist/index.js"
    ).read_bytes()
    assert (user / "vendor/dist/x86/magick.wasm").read_bytes() == (
        package / "dist/x86/magick.wasm"
    ).read_bytes()
    assert not (tmp_path / "selected-compat/main").exists()


def test_unpinned_or_symlinked_npm_cache_rejected(tmp_path):
    with pytest.raises(selected.SelectedEdgeRuntimeError, match="CACHE_MISSING"):
        selected._offline_codec_files(tmp_path)
    package = _cache(tmp_path)
    (package / "package.json").write_text(json.dumps({"version": "0.0.43"}))
    with pytest.raises(selected.SelectedEdgeRuntimeError, match="VERSION_MISMATCH"):
        selected._offline_codec_files(tmp_path)
    (package / "package.json").write_text(json.dumps({"version": "0.0.44"}))
    (package / "external").symlink_to(tmp_path / "outside")
    with pytest.raises(selected.SelectedEdgeRuntimeError, match="CACHE_UNSAFE"):
        selected._offline_codec_files(tmp_path)


def test_user_worker_source_handoff_is_gated_by_real_selected_runtime():
    source = (Path(__file__).resolve().parents[1]
              / "src/wonjae_dispatcher_runner/classmo_edge_preparation.py").read_text()
    assert source.index("prepare_selected_edge_runtime(root, _PNG_1PX)") < (
        source.index('(root / "local-smoke-pass").write_text(')
    )
    assert "CLASSMO_EDGE_SELECTED_RUNTIME_USER_WORKER_LOCAL_PASS_NOT_PROVIDER" in source
    assert "except SelectedEdgeRuntimeError as exc:" in source
    assert "raise EdgePrerequisiteError(str(exc)) from None" in source
    assert "verify_classmo_pr_identity(" in source
    assert "CLASSMO_EDGE_WASM_3_FORMATS_LOCAL_DENO_PASS_NOT_EDGE_RUNTIME" in edge._SMOKE


def test_user_worker_http_probe_requires_exact_marker_and_is_local(monkeypatch, tmp_path):
    # Stub process and response: this is not a claimed real Edge Runtime PASS.
    events = []

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            events.append("terminated")

        def wait(self, timeout):
            events.append(("wait", timeout))
            return 0

    class FakeResponse:
        status = 200

        def read(self, limit):
            assert limit == 200
            return selected.PASS_MARKER

    class FakeConnection:
        def __init__(self, host, port, timeout):
            assert host == "127.0.0.1"
            assert isinstance(port, int) and port > 0
            assert timeout == 3

        def request(self, method, path):
            assert (method, path) == ("GET", "/codec")

        def getresponse(self):
            return FakeResponse()

        def close(self):
            events.append("closed")

    def spawn(args, *, env, cwd, stdout, stderr):
        assert args[1:4] == ["start", "--ip", "127.0.0.1"]
        assert "--main-service" in args
        assert cwd == tmp_path
        assert "CLASSMO_EDGE_USER_DIR" in env
        assert "GH_TOKEN" not in env
        assert "PRODUCT_TOKEN" not in env
        assert "RUNNER_TOKEN" not in env
        assert "SUPABASE_ACCESS_TOKEN" not in env
        assert stdout == stderr == selected.subprocess.DEVNULL
        events.append("spawned")
        return FakeProcess()

    (tmp_path / "home").mkdir()
    _cache(tmp_path)
    monkeypatch.setattr(selected.subprocess, "Popen", spawn)
    monkeypatch.setattr(selected.http.client, "HTTPConnection", FakeConnection)
    selected._edge_worker_smoke(tmp_path / "selected-runtime/bin/edge-runtime",
                                tmp_path, "synthetic-base64")
    user_source = (tmp_path / "selected-compat/user/index.ts").read_text()
    assert "CLASSMO_SELECTED_EDGE_RUNTIME_USER_CODEC_PASS" in user_source
    assert "./vendor/dist/index.js" in user_source
    assert "npm:" not in user_source
    assert "spawned" in events
    assert "terminated" in events
    assert "closed" in events
