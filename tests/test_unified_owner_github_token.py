"""Common GitHub PAT is repository/Owner-scoped, not a Provider credential.

These are offline workflow mappings; no PAT values or GitHub API requests.
"""
from pathlib import Path

PROJECTS = ("WAFL", "CLASSMO", "ESC", "MUVEL")

ACTIVE = (
    "direct-worker.yml",
    "document-pr-merge.yml",
    "provider-gate-approval.yml",
    "source-pr-recovery-docs.yml",
    "automatic-source-recovery-docs.yml",
    "reconciliation-finalizer.yml",
    "product-terminal-guard.yml",
    "external-product-validation.yml",
)

LEGACY = (
    "wafl-product-v2.yml",
    "classmo-product-pilot.yml",
    "esc-product-v2.yml",
    "muvel-product-v2.yml",
)


def caller(filename: str) -> str:
    return (Path(".github/workflows") / filename).read_text(encoding="utf-8")


def test_owner_token_is_available_to_every_active_product_route() -> None:
    for filename in ACTIVE:
        raw = caller(filename)
        for project in PROJECTS:
            # New shared secret wins when present; existing per-project
            # secrets keep a safe fallback until the Owner explicitly rotates.
            expected = (
                "secrets.OWNER_GITHUB_TOKEN || "
                f"secrets.{project}_WRITE_TOKEN"
            )
            assert expected in raw, f"{filename} lacks {project} fallback"
        assert raw.count("secrets.OWNER_GITHUB_TOKEN || ") == len(PROJECTS)


def test_archived_product_workflows_accept_shared_token_without_cross_project() -> None:
    for project, filename in zip(PROJECTS, LEGACY):
        raw = caller(filename)
        expected = (
            "secrets.OWNER_GITHUB_TOKEN || "
            f"secrets.{project}_WRITE_TOKEN"
        )
        assert expected in raw
        for other in PROJECTS:
            if other != project:
                assert f"secrets.{other}_WRITE_TOKEN" not in raw


def test_shared_github_pat_never_replaces_external_services_or_control_secrets() -> None:
    provider = caller("provider-gate-approval.yml")
    source = caller("direct-worker.yml")
    merge = caller("common-control-pr-merge.yml")
    assert "secrets.CLASSMO_SUPABASE_ACCESS_TOKEN" in provider
    assert "secrets.WAFL_SUPABASE_ACCESS_TOKEN" in provider
    assert "secrets.CLASSMO_G1V_DEV_DB_PASSWORD" in provider
    assert "secrets.CLASSMO_EXPO_TOKEN" in provider
    assert "secrets.CODEX_AUTH_JSON" in source
    assert "secrets.CONTROL_READ_TOKEN" in source
    assert "secrets.SECRET_ROTATION_TOKEN" in source
    assert "secrets.COMMON_PR_MERGE_TOKEN" in merge
    # A GitHub repo PAT is not authority for credentials, Production,
    # Provider-side mutations, or registered product runs without Owner gates.
    assert "secrets.OWNER_GITHUB_TOKEN" not in merge


def test_legacy_provider_tokens_stay_isolated_until_separately_migrated() -> None:
    # No change to historical cross-provider authorization in this PR.
    for project, filename in (
        ("WAFL", "wafl-provider-v2.yml"),
        ("ESC", "esc-provider-v2.yml"),
        ("MUVEL", "muvel-provider-v2.yml"),
    ):
        text = caller(filename)
        assert f"secrets.{project}_PROVIDER_GITHUB_TOKEN" in text
        assert "secrets.OWNER_GITHUB_TOKEN" not in text
