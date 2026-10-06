from pathlib import Path


def test_external_product_validation_is_exact_sha_and_non_substituting() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/external-product-validation.yml").read_text(
        encoding="utf-8"
    )
    for project in ("WAFL", "CLASSMO", "ESC", "MUVEL"):
        assert f"[EXTERNAL-VALIDATE][{project}]" in workflow
        assert f"secrets.{project}_WRITE_TOKEN" in workflow
    assert "github.event.issue.user.login == github.repository_owner" in workflow
    assert "([0-9a-f]{40})" in workflow
    assert "canonical_product_workflow: `NOT_SUBSTITUTED`" in workflow
    assert "persist-credentials: false" in workflow
    assert "KDN" not in workflow
