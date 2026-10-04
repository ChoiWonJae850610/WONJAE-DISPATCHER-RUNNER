from wonjae_dispatcher_runner.source_queue import (
    QueueContractError,
    completion_matches_source_run,
    completion_source_sha,
    latest_queue_evidence,
    parse_execution_title,
    parse_registration_title,
    source_authority,
    terminal_result,
)

CONTROL_A = "a" * 40
CONTROL_B = "b" * 40
SOURCE_A = "1" * 40
INTEGRATED_A = "2" * 40


def exact_title() -> str:
    return (
        "[PRODUCT-WAKE][DISPATCHER-V2] CLASSMO "
        f"CLASSMO-QUEUE-A-001 {CONTROL_A} {SOURCE_A}"
    )


def queued_title() -> str:
    return (
        "[PRODUCT-QUEUE][DISPATCHER-V2] CLASSMO "
        f"CLASSMO-QUEUE-B-001 {CONTROL_B} 101"
    )


def queue_comment(state: str, source_sha: str = "") -> str:
    lines = [
        "## Dispatcher v2 source queue evidence",
        "",
        "- project: `CLASSMO`",
        "- task_id: `CLASSMO-QUEUE-B-001`",
        f"- control_sha: `{CONTROL_B}`",
        "- wake_issue: `102`",
        f"- queue_state: `{state}`",
        "- source_mode: `predecessor_integrated_sha`",
        "- workflow_file: `classmo-product-pilot.yml`",
        "- predecessor_issue: `101`",
        "- predecessor_task_id: `CLASSMO-QUEUE-A-001`",
        f"- predecessor_control_sha: `{CONTROL_A}`",
        "- predecessor_attempt: `1`",
    ]
    if source_sha:
        lines.append(f"- source_sha: `{source_sha}`")
    return "\n".join(lines)


def completed_comment(
    *,
    result: str = "COMPLETED",
    source_run_id: str = "7001",
    source_sha: str = SOURCE_A,
) -> str:
    return "\n".join(
        [
            "## Dispatcher v2 queue terminal evidence",
            "",
            "- project: `CLASSMO`",
            "- task_id: `CLASSMO-QUEUE-A-001`",
            "- attempt: `1`",
            f"- control_sha: `{CONTROL_A}`",
            f"- source_sha: `{source_sha}`",
            "- wake_issue: `101`",
            f"- result: `{result}`",
            f"- source_run_id: `{source_run_id}`",
            f"- integrated_sha: `{INTEGRATED_A}`",
        ]
    )


def terminal_comment(result: str) -> str:
    return "\n".join(
        [
            "## Dispatcher v2 terminal evidence",
            "",
            "- project: `CLASSMO`",
            "- task_id: `CLASSMO-QUEUE-A-001`",
            "- attempt: `1`",
            f"- control_sha: `{CONTROL_A}`",
            f"- source_sha: `{SOURCE_A}`",
            "- runner_run_id: `7001`",
            f"- result: `{result}`",
            "- recovery_state: `FINAL`",
        ]
    )


def test_exact_and_predecessor_bound_registration_titles_are_distinct() -> None:
    exact = parse_registration_title(exact_title())
    queued = parse_registration_title(queued_title())

    assert exact is not None
    assert exact.mode == "exact_source_sha"
    assert exact.exact_source_sha == SOURCE_A
    assert exact.predecessor_issue is None

    assert queued is not None
    assert queued.mode == "predecessor_integrated_sha"
    assert queued.predecessor_issue == 101
    assert queued.exact_source_sha == ""


def test_kdn_never_parses_as_source_queue_registration() -> None:
    title = (
        "[PRODUCT-QUEUE][DISPATCHER-V2] KDN "
        f"KDN-QUEUE-001 {CONTROL_B} 101"
    )
    assert parse_registration_title(title) is None


def test_schema_v1_requires_exact_source_sha() -> None:
    registration = parse_registration_title(exact_title())
    assert registration is not None
    record = {
        "schema_version": 1,
        "task_id": registration.task_id,
        "project": "CLASSMO",
        "operation_type": "source_change",
        "integration_authorized": True,
        "source_base_sha": SOURCE_A,
    }

    authority = source_authority(record, registration)

    assert authority.mode == "exact_source_sha"
    assert authority.exact_source_sha == SOURCE_A


def test_schema_v2_binds_source_to_exact_predecessor_identity() -> None:
    registration = parse_registration_title(queued_title())
    assert registration is not None
    record = {
        "schema_version": 2,
        "task_id": registration.task_id,
        "project": "CLASSMO",
        "operation_type": "source_change",
        "integration_authorized": True,
        "source_base": {
            "mode": "predecessor_integrated_sha",
            "predecessor_wake_issue": 101,
            "predecessor_task_id": "CLASSMO-QUEUE-A-001",
            "predecessor_control_sha": CONTROL_A,
            "predecessor_attempt": 1,
        },
    }

    authority = source_authority(record, registration)

    assert authority.mode == "predecessor_integrated_sha"
    assert authority.predecessor_issue == 101
    assert authority.predecessor_task_id == "CLASSMO-QUEUE-A-001"
    assert authority.predecessor_control_sha == CONTROL_A
    assert authority.predecessor_attempt == 1


def test_schema_v2_rejects_wrong_predecessor_issue() -> None:
    registration = parse_registration_title(queued_title())
    assert registration is not None
    record = {
        "schema_version": 2,
        "task_id": registration.task_id,
        "project": "CLASSMO",
        "operation_type": "source_change",
        "integration_authorized": True,
        "source_base": {
            "mode": "predecessor_integrated_sha",
            "predecessor_wake_issue": 999,
            "predecessor_task_id": "CLASSMO-QUEUE-A-001",
            "predecessor_control_sha": CONTROL_A,
            "predecessor_attempt": 1,
        },
    }

    try:
        source_authority(record, registration)
    except QueueContractError as exc:
        assert "predecessor issue mismatch" in str(exc)
    else:
        raise AssertionError("wrong predecessor issue must fail closed")


def test_exact_completed_evidence_resolves_only_integrated_sha() -> None:
    comments = [completed_comment()]

    assert (
        completion_source_sha(
            comments,
            task_id="CLASSMO-QUEUE-A-001",
            control_sha=CONTROL_A,
            attempt=1,
        )
        == INTEGRATED_A
    )
    assert (
        completion_source_sha(
            comments,
            task_id="CLASSMO-QUEUE-A-001",
            control_sha=CONTROL_A,
            attempt=2,
        )
        == ""
    )


def test_source_run_id_and_source_sha_must_match_before_advance() -> None:
    comments = [completed_comment()]

    assert completion_matches_source_run(
        comments,
        source_run_id="7001",
        source_sha=SOURCE_A,
    )
    assert not completion_matches_source_run(
        comments,
        source_run_id="7002",
        source_sha=SOURCE_A,
    )
    assert not completion_matches_source_run(
        comments,
        source_run_id="7001",
        source_sha="3" * 40,
    )


def test_failed_manual_and_cancelled_are_terminal_and_never_completed() -> None:
    for result in ("FAILED", "MANUAL_REQUIRED", "CANCELLED"):
        comments = [terminal_comment(result)]
        assert terminal_result(comments) == result
        assert (
            completion_source_sha(
                comments,
                task_id="CLASSMO-QUEUE-A-001",
                control_sha=CONTROL_A,
                attempt=1,
            )
            == ""
        )


def test_latest_queue_claim_controls_dispatch_state() -> None:
    queued = queue_comment("QUEUED")
    dispatched = queue_comment("DISPATCHED", INTEGRATED_A)

    evidence = latest_queue_evidence([queued, dispatched])

    assert evidence is not None
    assert evidence.state == "DISPATCHED"
    assert evidence.fields["source_sha"] == INTEGRATED_A


def test_execution_title_remains_exact_sha_bound_for_terminal_guard() -> None:
    parsed = parse_execution_title(exact_title())

    assert parsed == (
        "CLASSMO",
        "CLASSMO-QUEUE-A-001",
        CONTROL_A,
        SOURCE_A,
    )
