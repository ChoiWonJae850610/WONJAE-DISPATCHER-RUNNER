"""All GitHub personal-access-token workflow references use one Owner PAT.

Offline contract only: no token values, API calls, or workflow dispatch.
GitHub auto-generated github.token is not an Owner PAT.
"""
from pathlib import Path

WORKFLOW_ROOT = Path(".github/workflows")
PRODUCTS = ("WAFL", "CLASSMO", "ESC", "MUVEL")
LEGACY_PAT_NAMES = (
    "CONTROL_READ_TOKEN", "SECRET_ROTATION_TOKEN", "COMMON_PR_MERGE_TOKEN",
    *(f"{project}_WRITE_TOKEN" for project in PRODUCTS),
    *(f"{project}_PROVIDER_GITHUB_TOKEN" for project in PRODUCTS),
)
GITHUB_PAT_WORKFLOWS = (
    "direct-worker.yml", "document-pr-merge.yml", "provider-gate-approval.yml",
    "automatic-source-recovery-docs.yml", "source-pr-recovery-docs.yml",
    "reconciliation-finalizer.yml", "product-terminal-guard.yml",
    "external-product-validation.yml", "external-control-validation.yml",
    "source-queue-controller.yml", "provider-terminal-guard.yml",
    "common-control-pr-merge.yml", "bootstrap-codex-auth.yml",
    "direct-worker-home-codex-smoke.yml", "pilot-readonly.yml",
    "wafl-provider-v2.yml", "classmo-provider-v2.yml",
    "esc-provider-v2.yml", "muvel-provider-v2.yml",
    "wafl-product-v2.yml", "classmo-product-pilot.yml",
    "esc-product-v2.yml", "muvel-product-v2.yml",
)


def workflow(name: str) -> str:
    return (WORKFLOW_ROOT / name).read_text(encoding="utf-8")


def test_every_git_personal_token_reference_uses_one_owner_secret() -> None:
    files = sorted(WORKFLOW_ROOT.glob("*.yml"))
    assert len(files) >= 37
    for path in files:
        content = path.read_text(encoding="utf-8")
        for old in LEGACY_PAT_NAMES:
            assert f"secrets.{old}" not in content, (path.name, old)
    for name in GITHUB_PAT_WORKFLOWS:
        assert "secrets.OWNER_GITHUB_TOKEN" in workflow(name), name


def test_shared_pat_still_respects_product_and_owner_gates() -> None:
    source = workflow("direct-worker.yml")
    merge = workflow("common-control-pr-merge.yml")
    assert "workflow_dispatch:" in source
    assert "type: choice" in source
    for project in PRODUCTS:
        assert f"- {project}" in source
    assert "github.actor == github.repository_owner" in merge
    assert "secrets.OWNER_GITHUB_TOKEN" in merge
    assert "secrets.COMMON_PR_MERGE_APPROVAL_SECRET" in merge
    assert "COMMON_PR_MERGE_ACTIVATED" in merge
    assert "COMMON_PR_MERGE_TARGET_REPOSITORY" in merge


def test_cloudflare_api_pat_is_one_independent_provider_secret() -> None:
    provider = workflow("provider-gate-approval.yml")
    assert "secrets.CLOUDFLARE_API_TOKEN" in provider
    assert "secrets.WAFL_CLOUDFLARE_API_TOKEN" not in provider


def test_unrelated_service_credentials_are_not_replaced() -> None:
    source = workflow("direct-worker.yml")
    provider = workflow("provider-gate-approval.yml")
    assert "secrets.CODEX_AUTH_JSON" in source
    assert "secrets.CLASSMO_SUPABASE_ACCESS_TOKEN" in provider
    assert "secrets.WAFL_SUPABASE_ACCESS_TOKEN" in provider
    assert "secrets.CLASSMO_G1V_DEV_DB_PASSWORD" in provider
    assert "secrets.CLASSMO_EXPO_TOKEN" in provider
    assert "secrets.WAFL_EXPO_TOKEN" in provider
