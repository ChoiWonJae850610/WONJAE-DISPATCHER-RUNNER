from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_provider_gate_approval_caller_is_owner_button_recording_only():
    raw = (ROOT / ".github/workflows/provider-gate-approval.yml").read_text(encoding="utf-8")
    for value in ("project:", "control_sha:", "source_sha:", "gate:"):
        assert value in raw
    for job in ("wafl:", "classmo:", "esc:", "muvel:"):
        assert job in raw
    assert "Provider Gate Approval ${{ inputs.project }}" in raw
    assert "provider-gate-approval-core.yml" in raw
    assert "CONTROL_READ_TOKEN" in raw
    assert "WAFL_WRITE_TOKEN" in raw
    assert "CLASSMO_WRITE_TOKEN" in raw
    assert "ESC_WRITE_TOKEN" in raw
    assert "MUVEL_WRITE_TOKEN" in raw


def test_provider_gate_approval_core_has_exact_identity_guards_and_no_provider_mutation():
    raw = (ROOT / ".github/workflows/provider-gate-approval-core.yml").read_text(
        encoding="utf-8"
    )
    assert 'test "$current" = "$SOURCE_SHA"' in raw
    assert 'config["approval_mode"] == "sanjinworks_owner_button"' in raw
    assert 'config["executor"] == "approval_only"' in raw
    assert 'action["type"] == "PROVIDER_GATE"' in raw
    assert 'action["gate"] == gate' in raw
    assert "provider mutation: NOT_RUN" in raw
    assert "supabase" not in raw.lower()
    assert "eas update" not in raw.lower()
    assert "wrangler" not in raw.lower()
