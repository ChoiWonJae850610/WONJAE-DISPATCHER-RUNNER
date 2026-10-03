from pathlib import Path

SOURCE_WORKFLOWS = (
    ".github/workflows/classmo-product-pilot.yml",
    ".github/workflows/wafl-product-v2.yml",
    ".github/workflows/esc-product-v2.yml",
    ".github/workflows/muvel-product-v2.yml",
)

PROVIDER_ADAPTERS = (
    ".github/workflows/classmo-provider-v2.yml",
    ".github/workflows/wafl-provider-v2.yml",
    ".github/workflows/esc-provider-v2.yml",
    ".github/workflows/muvel-provider-v2.yml",
)


def test_source_workflows_gate_native_gmail_until_explicit_enable() -> None:
    for path in SOURCE_WORKFLOWS:
        text = Path(path).read_text(encoding="utf-8")
        assert "GITHUB_NATIVE_GMAIL_ENABLED" in text
        assert "GMAIL_USERNAME" in text
        assert "GMAIL_APP_PASSWORD" in text
        assert "gmail_notify.py" in text
        assert "GitHub-native STARTED" in text
        assert "GitHub-native COMPLETED" in text
        assert "GitHub-native terminal failure" in text


def test_source_terminal_guard_has_idempotent_native_gmail_fallback() -> None:
    text = Path(".github/workflows/product-terminal-guard.yml").read_text(encoding="utf-8")
    assert "GITHUB_NATIVE_GMAIL_ENABLED" in text
    assert "CONTROL_READ_TOKEN" in text
    assert "GMAIL_USERNAME" in text
    assert "GMAIL_APP_PASSWORD" in text
    assert "gmail_notify.py" in text
    assert "reconcile terminal notification" in text


def test_provider_reusable_workflows_accept_gmail_credentials() -> None:
    for path in (
        ".github/workflows/provider-action-core.yml",
        ".github/workflows/provider-status-core.yml",
    ):
        text = Path(path).read_text(encoding="utf-8")
        assert "gmail_enabled:" in text
        assert "gmail_username:" in text
        assert "gmail_app_password:" in text
        assert "gmail_notify.py" in text


def test_provider_adapters_pass_feature_flag_and_gmail_secrets() -> None:
    for path in PROVIDER_ADAPTERS:
        text = Path(path).read_text(encoding="utf-8")
        assert "GITHUB_NATIVE_GMAIL_ENABLED" in text
        assert "gmail_username: ${{ secrets.GMAIL_USERNAME }}" in text
        assert "gmail_app_password: ${{ secrets.GMAIL_APP_PASSWORD }}" in text


def test_provider_terminal_guard_can_reconcile_native_gmail() -> None:
    text = Path(".github/workflows/provider-terminal-guard.yml").read_text(encoding="utf-8")
    assert "GITHUB_NATIVE_GMAIL_ENABLED" in text
    assert "PROVIDER_TERMINAL_RESULT" in text
    assert "gmail_notify.py" in text
    assert "CONTROL_READ_TOKEN" in text


def test_gmail_smoke_is_manual_and_has_no_product_mutation() -> None:
    text = Path(".github/workflows/gmail-notification-smoke.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "GMAIL_USERNAME" in text
    assert "GMAIL_APP_PASSWORD" in text
    assert "--cleanup-only" in text
    assert "git push" not in text
    assert "gh pr" not in text
