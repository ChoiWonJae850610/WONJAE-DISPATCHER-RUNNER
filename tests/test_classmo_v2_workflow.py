from pathlib import Path

WORKFLOW = Path(".github/workflows/classmo-product-pilot.yml")


def test_classmo_v2_wake_is_task_generic() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: CLASSMO Dispatcher v2" in text
    assert "[PRODUCT-WAKE][DISPATCHER-V2] CLASSMO " in text
    assert "CLASSMO-V2-CORE-UX-INTEGRATION-POLISH-001" not in text
    assert "tasks-v2/CLASSMO/{task_id}.json" in text
    assert '"task_id": os.environ["TASK_ID"]' in text


def test_classmo_v2_pr_and_result_are_observable() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for field in (
        "dispatcher_version",
        "task_id",
        "attempt",
        "revision",
        "profile",
        "control_sha",
        "control_record_path",
        "source_base_sha",
    ):
        assert field in text
    assert "## Dispatcher v2 completion evidence" in text
    assert "exact_pr_head_validation_run" in text
    assert "exact_integrated_sha_validation_run" in text


def test_classmo_v2_guards_concurrent_job_prs() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Another CLASSMO job pull request is already open." in text
    assert 'startswith("job/")' in text


def test_classmo_v2_recovers_failed_pr_validation_before_terminal_failure() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "exact PR-head Actions with bounded recovery" in text
    assert "max_repairs=2" in text
    assert "run_product_repair.py" in text
    assert "## Dispatcher v2 terminal evidence" in text
