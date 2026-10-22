from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / ".github/workflows/provider-gate-approval-core.yml"
CALLER = ROOT / ".github/workflows/provider-gate-approval.yml"


def test_esc_signed_preview_requires_exact_owner_registered_scope():
    raw = CORE.read_text(encoding="utf-8")
    esc = raw.split('elsif executor == "esc_eas_build"', 1)[1].split(
        'elsif executor == "classmo_eas_update"', 1
    )[0]
    for value in (
        'project == "ESC" && gate == "preview_ios_build"',
        '"provider" => "eas_build"',
        '"working_directory" => "apps/mobile"',
        '"platform" => "ios"',
        '"profile" => "preview"',
        '"environment" => "preview"',
        '"channel" => "preview"',
        '"app_version" => "0.1.1"',
        '"build_number" => "2"',
        '"runtime_version" => "0.1.1"',
        '"icon_path" => "assets/branding/esc-app-icon.png"',
    ):
        assert value in esc


def test_esc_build_preflight_checks_icon_eas_identity_and_reuses_guarded_eas_path():
    raw = CORE.read_text(encoding="utf-8")
    esc = raw.split("name: ESC · verify approved pixel-terminal icon source", 1)[1].split(
        "name: CLASSMO · verify preview build source contract", 1
    )[0]
    for value in (
        'env.PROVIDER_EXECUTOR == \'esc_eas_build\'',
        'com.sanjinworks.esc',
        'appVersion',
        'icon[25] != 2',
        'ESC icon must be 1024x1024',
        'ESC app icon reference mismatch',
        'ESC EAS project ID mismatch',
        'internal',
    ):
        assert value in esc
    for step in (
        "CLASSMO · read-only existing Preview build preflight",
        "CLASSMO · build signed internal preview",
        "CLASSMO · verify signed preview build result",
    ):
        body = raw.split("name: " + step, 1)[1].split("\n\n", 1)[0]
        assert "env.PROVIDER_EXECUTOR == 'esc_eas_build'" in body
    assert "parse-classmo-build-list" in raw  # exact-version/idempotency helper
    assert '--app-identifier "${PROVIDER_APP_IDENTIFIER:-com.sanjinworks.classmo}"' in raw
    assert '"PROVIDER_APP_IDENTIFIER" => "com.sanjinworks.esc"' in raw
    assert "--freeze-credentials" in raw
    assert 'test "$EAS_BUILD_APP_BUILD_VERSION" = "$PROVIDER_BUILD_NUMBER"' in raw
    assert 'test "$EAS_BUILD_ARCHIVE_PRESENT" = "1"' in raw


def test_esc_uses_only_esc_expo_secret_for_owner_gate():
    raw = CALLER.read_text(encoding="utf-8")
    esc = raw.split("\n  esc:\n", 1)[1].split("\n  muvel:\n", 1)[0]
    assert 'expo_token: ${{ secrets.ESC_EXPO_TOKEN }}' in esc
    assert 'product_read_token: ${{ secrets.OWNER_GITHUB_TOKEN }}' in esc
    assert 'WAFL_EXPO_TOKEN' not in esc
    assert 'CLASSMO_EXPO_TOKEN' not in esc
