from pathlib import Path

PRODUCTS = {
    "WAFL": {
        "workflow": ".github/workflows/wafl-product-v2.yml",
        "secret": "WAFL_WRITE_TOKEN",
        "validation": "WAFL Cloud Validation",
    },
    "ESC": {
        "workflow": ".github/workflows/esc-product-v2.yml",
        "secret": "ESC_WRITE_TOKEN",
        "validation": "Validate ESC",
    },
    "MUVEL": {
        "workflow": ".github/workflows/muvel-product-v2.yml",
        "secret": "MUVEL_WRITE_TOKEN",
        "validation": "MUVEL Cloud Development",
    },
}


def test_product_v2_workflows_are_isolated_and_task_generic() -> None:
    for project, config in PRODUCTS.items():
        text = Path(config["workflow"]).read_text(encoding="utf-8")
        assert f"name: {project} Dispatcher v2" in text
        assert f"[PRODUCT-WAKE][DISPATCHER-V2] {project} " in text
        assert f"tasks-v2/{project}/{{task_id}}.json" in text
        assert f"ChoiWonJae850610/{project}" in text
        assert config["secret"] in text
        assert config["validation"] in text
        assert "## Dispatcher v2 metadata" in text
        assert "## Dispatcher v2 completion evidence" in text
        assert "source_pr_lifecycle.py" in text
        assert "diagnose-conflicts" in text
        assert "PRODUCT_GH_TOKEN" in text
        assert "RUNNER_GH_TOKEN" in text


def test_product_v2_workflows_do_not_cross_use_write_tokens() -> None:
    all_tokens = {value["secret"] for value in PRODUCTS.values()} | {"CLASSMO_WRITE_TOKEN"}
    for _project, config in PRODUCTS.items():
        text = Path(config["workflow"]).read_text(encoding="utf-8")
        assert config["secret"] in text
        for token in all_tokens - {config["secret"]}:
            assert token not in text


def test_product_v2_workflows_recover_validation_and_terminalize_failure() -> None:
    for _project, config in PRODUCTS.items():
        text = Path(config["workflow"]).read_text(encoding="utf-8")
        assert "exact PR-head Actions with bounded recovery" in text
        assert "max_repairs=2" in text
        assert "run_product_repair.py" in text
        assert "Repair validation attempt" in text
        assert "## Dispatcher v2 terminal evidence" in text
        assert "recovery_state: `FINAL`" in text
        assert "source_pr_lifecycle.py" in text
        assert "terminalize" in text
        assert "product_pr_cleanup" in text
        assert "cleanup_residue" in text
        assert "gh issue close" in text
        assert "timeout-minutes: 60" in text


def test_terminal_guard_covers_all_source_dispatchers() -> None:
    text = Path(".github/workflows/product-terminal-guard.yml").read_text(encoding="utf-8")
    for project in ("CLASSMO", "WAFL", "ESC", "MUVEL"):
        assert f"{project} Dispatcher v2" in text
    assert "workflow_run:" in text
    assert "## Dispatcher v2 terminal evidence" in text
    assert "MANUAL_REQUIRED" in text
    assert "CANCELLED" in text
    assert "FAILED" in text
    assert "gh issue close" in text


def test_terminal_guard_binds_wake_project_to_source_workflow() -> None:
    text = Path(".github/workflows/product-terminal-guard.yml").read_text(encoding="utf-8")
    assert "SOURCE_WORKFLOW_NAME" in text
    assert 'expected_workflow = f"{project} Dispatcher v2"' in text
    assert "GUARD_APPLICABLE" in text
    assert "GUARD_APPLICABLE" in text
    assert "GUARD_ACTION" in text


def test_product_v2_higher_attempts_use_fresh_branches_and_track_repair_heads() -> None:
    for _project, config in PRODUCTS.items():
        text = Path(config["workflow"]).read_text(encoding="utf-8")
        assert '("" if attempt == 1 else f"-a{attempt}")' in text
        assert 'echo "PILOT_HEAD_SHA=$repaired_head" >> "$GITHUB_ENV"' in text


def test_completed_source_path_still_merges_only_validated_head() -> None:
    for _project, config in PRODUCTS.items():
        text = Path(config["workflow"]).read_text(encoding="utf-8")
        assert "exact PR-head Actions with bounded recovery" in text
        assert 'merge_method=merge' in text
        assert '-f sha="$PILOT_HEAD_SHA"' in text
        assert "exact integrated-SHA Actions" in text
        assert "Exact integrated-SHA validation did not succeed." in text


def test_source_terminal_guard_closes_only_exact_product_pr() -> None:
    text = Path(".github/workflows/product-terminal-guard.yml").read_text(encoding="utf-8")
    for project in ("CLASSMO", "WAFL", "ESC", "MUVEL"):
        assert f"Close exact {project} terminal Product PR" in text
        assert f"secrets.{project}_WRITE_TOKEN" in text
    assert "source_pr_lifecycle.py" in text
    assert "FINAL_GUARD" in text
    assert "terminal PR cleanup evidence" in text
    assert "product_pr_cleanup" in text
    assert "cleanup_residue" in text
    assert "delete-branch" not in text
    assert "git push --delete" not in text


def test_terminal_guard_records_cleanup_residue_even_if_close_step_errors() -> None:
    text = Path(".github/workflows/product-terminal-guard.yml").read_text(encoding="utf-8")
    for project in ("CLASSMO", "WAFL", "ESC", "MUVEL"):
        marker = f"- name: Close exact {project} terminal Product PR"
        start = text.index(marker)
        block = text[start : start + 260]
        assert "continue-on-error: true" in block
    assert "always() && env.GUARD_ACTION == 'TERMINALIZE'" in text
    assert 'SOURCE_PR_CLEANUP_STATUS:-RESIDUE' in text
