from pathlib import Path

import yaml


def test_control_static_validator_is_exact_owner_and_read_only():
    path = Path(__file__).parents[1] / ".github/workflows/external-control-validation.yml"
    text = path.read_text()
    payload = yaml.safe_load(text)
    assert payload["permissions"] == {"contents": "read", "issues": "write"}
    assert "github.event.issue.user.login == github.repository_owner" in text
    assert "[EXTERNAL-CONTROL-VALIDATE]" in text
    assert "ref: ${{ steps.claim.outputs.sha }}" in text
    assert "persist-credentials: false" in text
    assert "ruby .github/scripts/validate_control.rb" in text
    assert "CONTROL_READ_TOKEN" in text
    assert "WRITE_TOKEN" not in text
    assert "NOT_SUBSTITUTED" in text
