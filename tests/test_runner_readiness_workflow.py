"""Synthetic guard: Owner readiness workflow must remain nonsecret and non-mutating."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

def test_nonsecret_readiness_workflow_does_not_start_product_source_or_rotate_secrets():
    workflow = ROOT / ".github/workflows/direct-worker-runner-readiness.yml"
    text = workflow.read_text(encoding="utf-8")
    value = yaml.safe_load(text)
    trigger = value.get("on", value.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert set(trigger["workflow_dispatch"]["inputs"]) == {"project", "source_sha", "control_sha"}
    assert value["permissions"] == {}
    assert set(value["jobs"]) == {"readiness"}
    job = value["jobs"]["readiness"]
    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "direct-worker"]
    assert job["timeout-minutes"] <= 5
    for forbidden in ("CODEX_AUTH_JSON", "PRODUCT_WRITE_TOKEN", "CONTROL_READ_TOKEN",
                      "gh secret set", "pull_request_target", "git push", "git merge",
                      "git reset", "gh workflow run", "eas build", "wrangler deploy"):
        assert forbidden not in text
    assert "api.openai.com" in text
    assert "getent hosts" in text
    assert "A successful workflow proves only these host checks" in text
