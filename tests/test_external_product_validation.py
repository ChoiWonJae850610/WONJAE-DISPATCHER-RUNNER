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



def test_external_validation_has_dispatch_identity_and_fallback_scope() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/external-product-validation.yml").read_text(
        encoding="utf-8"
    )
    assert "run-name:" in workflow
    assert "External Product Validation {0} {1}" in workflow
    assert "RUNNER_ALLOCATION_FAILURE_ONLY" in workflow


def test_direct_worker_uses_validation_gate_for_all_exact_sha_stages() -> None:
    root = Path(__file__).resolve().parents[1]
    caller = (root / ".github/workflows/direct-worker.yml").read_text(encoding="utf-8")
    core = (root / ".github/workflows/direct-worker-core.yml").read_text(encoding="utf-8")
    assert "actions: write" in caller
    assert "actions: write" in core
    assert core.count("scripts/validation_gate.py") == 3
    assert "--output-prefix START" in core
    assert "--output-prefix PR_HEAD" in core
    assert "--output-prefix INTEGRATED" in core
    assert "pr_head_validation_mode" in core
    assert "integrated_validation_mode" in core
