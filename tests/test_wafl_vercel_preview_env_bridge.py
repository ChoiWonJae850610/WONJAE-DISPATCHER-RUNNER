"""Synthetic fail-closed checks; no network, no Vercel/Secret writes."""
import importlib.util
from pathlib import Path

import pytest


def bridge_module():
    spec = importlib.util.spec_from_file_location(
        "wafl_vercel_preview_env_bridge",
        Path("scripts/wafl_vercel_preview_env_bridge.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_env(module):
    return {
        "GITHUB_REPOSITORY": module.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "WAFL_VERCEL_TOKEN": "synthetic-owner-api-token",
        "WAFL_R2_PREVIEW_WORKER_URL": "https://wafl-r2-dev.dev-owner.workers.dev",
        "WAFL_R2_WORKER_UPLOAD_SECRET": "synthetic-unique-r2-signing-key-" + "a" * 40,
        "WAFL_PDF_DEV_WORKER_URL": "https://wafl-pdf-dev.dev-owner.workers.dev",
        "WAFL_PDF_GENERATOR_TOKEN": "synthetic-unique-pdf-bearer-" + "b" * 40,
    }


def test_preview_values_are_scoped_and_sensitively_typed():
    module = bridge_module()
    env = sample_env(module)
    _token, body = module.required_values(env)
    assert [(item["key"], item["type"]) for item in body] == [
        ("R2_WORKER_UPLOAD_URL", "encrypted"),
        ("R2_WORKER_UPLOAD_SECRET", "sensitive"),
        ("WAFLOW_PDF_GENERATOR_URL", "encrypted"),
        ("WAFLOW_PDF_GENERATOR_TOKEN", "sensitive"),
    ]
    assert all(item["target"] == ["preview"] and item["gitBranch"] == module.BRANCH
               and "production" not in item["target"] for item in body)
    assert "upsert=false" in module.route("/env", write=True)
    assert "decrypt=false" in module.route("/env")


def test_wrong_workflow_and_missing_token_do_not_call_api(monkeypatch):
    module = bridge_module()
    monkeypatch.setattr(module, "http_json", lambda *_a, **_kw: pytest.fail("network attempted"))
    env = sample_env(module)
    env["GITHUB_REF"] = "refs/pull/1/merge"
    with pytest.raises(module.BridgeBlocked, match="TRUSTED_OWNER"):
        module.bridge(env, execute=True)
    env = sample_env(module)
    env.pop("WAFL_VERCEL_TOKEN")
    with pytest.raises(module.BridgeBlocked, match="API_CREDENTIAL_MISSING"):
        module.bridge(env, execute=True)


def test_wrong_dev_worker_and_secrets_block_before_network(monkeypatch):
    module = bridge_module()
    monkeypatch.setattr(module, "http_json", lambda *_a, **_kw: pytest.fail("network attempted"))
    env = sample_env(module)
    env["WAFL_R2_PREVIEW_WORKER_URL"] = "https://peacebypiece-r2-upload.legacy.workers.dev"
    with pytest.raises(module.BridgeBlocked, match="R2_ORIGIN_NOT_VERIFIED"):
        module.bridge(env, execute=True)
    env = sample_env(module)
    env["WAFL_PDF_GENERATOR_TOKEN"] = env["WAFL_R2_WORKER_UPLOAD_SECRET"]
    with pytest.raises(module.BridgeBlocked, match="SIGNING_TOKEN_IDENTITY_INVALID"):
        module.bridge(env, execute=True)


def test_existing_key_in_any_environment_never_posts(monkeypatch):
    module = bridge_module()
    events = []

    def fake(method, route, token, body=None):
        events.append(method)
        if route.startswith("/v9/projects/") and "/env" not in route:
            return {"id": module.PROJECT_ID, "name": module.PROJECT_NAME,
                    "accountId": module.TEAM_ID}
        return {"envs": [{"key": "R2_WORKER_UPLOAD_SECRET", "target": ["production"]}],
                "hiddenProductionEnvCount": 0}
    monkeypatch.setattr(module, "http_json", fake)
    with pytest.raises(module.BridgeBlocked, match="ALREADY_EXISTS"):
        module.bridge(sample_env(module), execute=True)
    assert events == ["GET", "GET"]


def test_wrong_team_and_ambiguous_inventory_never_posts(monkeypatch):
    module = bridge_module()
    events = []
    def wrong_team(method, route, token, body=None):
        events.append(method)
        return {"id": module.PROJECT_ID, "name": module.PROJECT_NAME, "accountId": "other"}
    monkeypatch.setattr(module, "http_json", wrong_team)
    with pytest.raises(module.BridgeBlocked, match="EXACT_TARGET_UNVERIFIED"):
        module.bridge(sample_env(module), execute=True)
    assert events == ["GET"]

    def hidden_metadata(method, route, token, body=None):
        events.append(method)
        if "/env" not in route:
            return {"id": module.PROJECT_ID, "name": module.PROJECT_NAME,
                    "accountId": module.TEAM_ID}
        return {"envs": [], "hiddenProductionEnvCount": 1}
    events.clear()
    monkeypatch.setattr(module, "http_json", hidden_metadata)
    with pytest.raises(module.BridgeBlocked, match="HIDDEN_PRODUCTION"):
        module.bridge(sample_env(module), execute=True)
    assert events == ["GET", "GET"]


def test_inspect_is_readonly_and_owner_apply_writes_one_batch(monkeypatch):
    module = bridge_module()
    methods = []
    written = []

    def fake(method, route, token, body=None):
        methods.append(method)
        if "/env" not in route:
            return {"id": module.PROJECT_ID, "name": module.PROJECT_NAME,
                    "accountId": module.TEAM_ID}
        if method == "POST":
            written.append(body)
            return [{"created": True}]
        if not written:
            return {"envs": [], "hiddenProductionEnvCount": 0}
        return {"envs": [
            {k: item[k] for k in ("key", "type", "target", "gitBranch")}
            for item in written[0]
        ], "hiddenProductionEnvCount": 0}
    monkeypatch.setattr(module, "http_json", fake)
    assert module.bridge(sample_env(module), execute=False) == "WAFL_VERCEL_PREVIEW_READ_ONLY_ELIGIBLE"
    assert written == []
    assert module.bridge(sample_env(module), execute=True) == "WAFL_VERCEL_PREVIEW_FOUR_ENV_METADATA_PASS"
    assert len(written) == 1 and len(written[0]) == 4
    assert methods == ["GET", "GET", "GET", "GET", "POST", "GET"]
    assert all(item["target"] == ["preview"] for item in written[0])


def test_ambiguous_post_never_retries(monkeypatch):
    module = bridge_module()
    post_count = []

    def fake(method, route, token, body=None):
        if method == "POST":
            post_count.append(1)
            raise module.BridgeBlocked("WAFL_VERCEL_API_RESPONSE_UNVERIFIED")
        if "/env" not in route:
            return {"id": module.PROJECT_ID, "name": module.PROJECT_NAME,
                    "accountId": module.TEAM_ID}
        return {"envs": [], "hiddenProductionEnvCount": 0}
    monkeypatch.setattr(module, "http_json", fake)
    with pytest.raises(module.BridgeBlocked, match="RESPONSE_UNVERIFIED"):
        module.bridge(sample_env(module), execute=True)
    assert len(post_count) == 1
