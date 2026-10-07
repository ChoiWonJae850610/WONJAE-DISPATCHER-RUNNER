def test_provider_gate_approval_caller_routes_exact_products_and_classmo_credential():
    with open(".github/workflows/provider-gate-approval.yml", encoding="utf-8") as handle:
        raw = handle.read()
    for value in ("project:", "control_sha:", "source_sha:", "gate:"):
        assert value in raw
    for job in ("wafl:", "classmo:", "esc:", "muvel:"):
        assert job in raw
    assert "Provider Gate Approval ${{ inputs.project }}" in raw
    assert "provider-gate-approval-core.yml" in raw
    assert "CONTROL_READ_TOKEN" in raw
    assert "CLASSMO_SUPABASE_ACCESS_TOKEN" in raw
    assert "CLASSMO_EXPO_TOKEN" in raw
    assert "WAFL_SUPABASE_ACCESS_TOKEN" in raw
    assert "WAFL_WRITE_TOKEN" in raw
    assert "CLASSMO_WRITE_TOKEN" in raw
    assert "ESC_WRITE_TOKEN" in raw
    assert "MUVEL_WRITE_TOKEN" in raw


def test_provider_gate_core_rechecks_identity_validation_and_registered_executor():
    with open(".github/workflows/provider-gate-approval-core.yml", encoding="utf-8") as handle:
        raw = handle.read()
    assert 'test "$current" = "$SOURCE_SHA"' in raw
    assert 'config["approval_mode"] == "sanjinworks_owner_button"' in raw
    assert (
        '["approval_only", "classmo_supabase_migration", '
        '"classmo_eas_build", "classmo_eas_update", '
        '"wafl_supabase_hardening"].include?(executor)' in raw
    )
    assert 'action["type"] == "PROVIDER_GATE"' in raw
    assert 'action["gate"] == gate' in raw
    assert "Exact current source has no successful integrated validation" in raw
    assert "Source-writing PR is open; provider execution refuses concurrent mutation" in raw
    assert "CLASSMO_SUPABASE_ACCESS_TOKEN is not configured" in raw
    assert "scripts/classmo_supabase_gate.py" in raw
    assert "scripts/wafl_supabase_gate.py" in raw
    assert 'executor == "classmo_eas_build"' in raw
    assert "CLASSMO_EXPO_TOKEN is not configured" in raw
    assert 'eas build \\' in raw
    assert 'parse-eas-build-view' in raw
    assert 'build_env="$RUNNER_TEMP/classmo-eas-build.env"' in raw
    assert 'cat "$build_env" >> "$GITHUB_ENV"' in raw
    assert 'device install / physical QA: `NOT_RUN`' in raw
    assert "provider mutation: `NOT_RUN`" in raw


def test_classmo_supabase_executor_is_bounded_to_migration_runtime_and_cleanup():
    with open("scripts/classmo_supabase_gate.py", encoding="utf-8") as handle:
        raw = handle.read()
    assert 'API_ROOT = "https://api.supabase.com/v1/projects"' in raw
    assert '"/database/migrations"' in raw
    assert '"/database/query"' in raw
    assert "migration checksum does not match SHA256SUMS" in raw
    assert "live migration history advanced beyond the registered provider gate" in raw
    assert "option_entry.value->>'id'" in raw
    assert "COURSE_ZERO_RESIDUE" in raw
    assert "independent concurrency verification" in raw
    assert "NOT_RUN" in raw
    assert "Production / EAS / OTA / device / physical" in raw


def test_wafl_supabase_executor_is_bounded_to_stage4_hardening():
    with open("scripts/wafl_supabase_gate.py", encoding="utf-8") as handle:
        raw = handle.read()
    assert 'MIGRATION_NAME = "wafl_stage4_post_apply_hardening"' in raw
    assert (
        'MIGRATION_PATH = '
        '"supabase/migrations/202610070001_wafl_stage4_post_apply_hardening.sql"'
        in raw
    )
    assert 'EXPECTED_PRIOR_MIGRATION = "wafl_stage4_target_schema"' in raw
    assert "company_members_company_user_unique" in raw
    assert "company_members_company_id_user_id_key" in raw
    assert "wafl_private" in raw
    assert "/database/migrations" in raw
    assert "/database/query" in raw
    assert "/postgrest" in raw
    assert "data copy / identity mapping / Production / device" in raw

def test_classmo_preview_ota_provider_gate_is_bounded_and_read_back():
    with open(".github/workflows/provider-gate-approval-core.yml", encoding="utf-8") as handle:
        raw = handle.read()
    assert 'executor == "classmo_eas_update"' in raw
    assert 'gate == "preview_ios_ota_update"' in raw
    assert '"provider" => "eas_update"' in raw
    assert '"working_directory" => "apps/mobile"' in raw
    assert '"platform" => "ios"' in raw
    assert '"environment" => "preview"' in raw
    assert '"channel" => "preview"' in raw
    assert '"runtime_version" => "0.0.11"' in raw
    assert '"compatibility_base_sha" => "bc18af6cab37408643f3825e1137a75dd4b899f3"' in raw
    assert 'merge-base --is-ancestor "$PROVIDER_COMPATIBILITY_BASE_SHA" "$SOURCE_SHA"' in raw
    assert "provider_action.py check-ota" in raw
    assert 'eas update \\' in raw
    assert "parse-update-group" in raw
    assert 'eas update:view "$EAS_UPDATE_GROUP_ID"' in raw
    assert "parse-update-view" in raw
    assert 'device receipt / physical QA: `NOT_RUN`' in raw

