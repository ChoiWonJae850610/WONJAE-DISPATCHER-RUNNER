from wonjae_dispatcher_runner.validation_fallback import (
    is_runner_allocation_failure,
    matching_external_success,
)


SHA = "a" * 40


def canonical_run(**overrides):
    value = {
        "status": "completed",
        "conclusion": "failure",
        "created_at": "2026-10-06T02:57:49Z",
    }
    value.update(overrides)
    return value


def unassigned_job(**overrides):
    value = {"runner_id": 0, "runner_name": "", "steps": []}
    value.update(overrides)
    return value


def test_runner_allocation_failure_requires_every_job_unassigned_and_step_free():
    assert is_runner_allocation_failure(canonical_run(), [unassigned_job()])
    assert is_runner_allocation_failure(
        canonical_run(), [unassigned_job(), unassigned_job()]
    )
    assert not is_runner_allocation_failure(
        canonical_run(), [unassigned_job(runner_id=42)]
    )
    assert not is_runner_allocation_failure(
        canonical_run(), [unassigned_job(steps=[{"name": "Checkout"}])]
    )
    assert not is_runner_allocation_failure(
        canonical_run(conclusion="cancelled"), [unassigned_job()]
    )
    assert not is_runner_allocation_failure(canonical_run(), [])


def test_external_success_must_match_exact_project_sha_workflow_and_be_new_enough():
    runs = [
        {
            "id": 10,
            "path": ".github/workflows/external-product-validation.yml",
            "event": "issues",
            "display_title": f"[EXTERNAL-VALIDATE][WAFL] {SHA}",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-10-06T03:50:15Z",
        },
        {
            "id": 11,
            "path": ".github/workflows/external-product-validation.yml",
            "event": "workflow_dispatch",
            "display_title": f"External Product Validation WAFL {SHA}",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-10-06T03:55:15Z",
        },
    ]
    found = matching_external_success(
        runs,
        project="WAFL",
        source_sha=SHA,
        not_before="2026-10-06T02:57:49Z",
    )
    assert found is not None and found["id"] == 11


def test_external_success_does_not_override_newer_canonical_failure():
    found = matching_external_success(
        [
            {
                "id": 10,
                "path": ".github/workflows/external-product-validation.yml",
                "event": "issues",
                "display_title": f"[EXTERNAL-VALIDATE][WAFL] {SHA}",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-06T02:00:00Z",
            }
        ],
        project="WAFL",
        source_sha=SHA,
        not_before="2026-10-06T02:57:49Z",
    )
    assert found is None
