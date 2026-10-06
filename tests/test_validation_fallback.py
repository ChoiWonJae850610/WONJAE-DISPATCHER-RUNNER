import json
from pathlib import Path

import pytest

from wonjae_dispatcher_runner.validation_fallback import (
    exact_canonical,
    is_runner_allocation_failure,
    matching_external_success,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/validation-policy.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_shared_fallback_policy(case):
    run = FIXTURE["canonical"] | case.get("canonical", {})
    jobs = case.get("jobs", FIXTURE["jobs"])
    external = FIXTURE["external"] | case.get("external", {})
    allowed = (
        exact_canonical(run, repository=FIXTURE["repository"],
                        workflow_path=FIXTURE["workflow_path"],
                        source_sha=FIXTURE["source_sha"], event="push", run_id=42)
        and is_runner_allocation_failure(run, jobs)
        and matching_external_success(
            [external], project=FIXTURE["project"], source_sha=FIXTURE["source_sha"],
            not_before=run["updated_at"], workflow_id=FIXTURE["workflow_id"],
        ) is not None
    )
    assert allowed is case["allow"]
