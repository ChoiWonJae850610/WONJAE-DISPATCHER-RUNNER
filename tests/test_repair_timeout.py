import signal
import time
from dataclasses import replace

import pytest
from test_direct_worker import registry

from wonjae_dispatcher_runner import direct_worker as worker
from wonjae_dispatcher_runner.classmo_edge_preparation import (
    SOURCE_BRANCH,
    SOURCE_REPO,
    SOURCE_TASK,
)
from wonjae_dispatcher_runner.execution_state import ExecutionAction, ProductExecutionState
from wonjae_dispatcher_runner.repair_timeout import (
    EXTENDED_SOURCE_TURN_TIMEOUT_SECONDS,
    REPAIR_PLAN_TIMEOUT_SECONDS,
    RepairPlanTimeout,
    extended_source_turn_deadline,
    repair_plan_deadline,
)


def test_wall_clock_interrupts_hang_and_restores_timer_handler():
    before = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    with pytest.raises(RepairPlanTimeout, match="wall-clock deadline"):
        with repair_plan_deadline(0.02):
            time.sleep(1)
    assert time.monotonic() - started < 0.5
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == before
    with repair_plan_deadline(0.02):
        pass


@pytest.mark.parametrize("seconds", [0, -1, REPAIR_PLAN_TIMEOUT_SECONDS + 1])
def test_deadline_cannot_be_disabled_or_extended(seconds):
    with pytest.raises(ValueError):
        with repair_plan_deadline(seconds):
            pytest.fail("invalid deadline entered")


def test_exact_classmo_source_retry_has_bounded_extension_only(tmp_path):
    route = worker.load_direct_worker_route(registry(tmp_path), "ESC")
    classmo = replace(
        route, project="CLASSMO", repository=SOURCE_REPO,
        branch=SOURCE_BRANCH, state_path=".wonjae/execution-state.yaml",
    )
    action = ExecutionAction(
        type="SOURCE_READY", title="027-P2 image backend", source_task_id=SOURCE_TASK,
        owner_action=None, source_scope=("Source only, no provider",), gate=None,
    )
    state = ProductExecutionState(
        project="CLASSMO", path=".wonjae/execution-state.yaml",
        next_action=action, after_source_success=None,
    )
    assert EXTENDED_SOURCE_TURN_TIMEOUT_SECONDS == 960
    assert REPAIR_PLAN_TIMEOUT_SECONDS == 480
    for command in ("retry", "resume"):
        assert worker.source_turn_timeout_seconds(classmo, command, state) == 960
    for command in ("next", "invalid"):
        assert worker.source_turn_timeout_seconds(classmo, command, state) == 480
    assert worker.source_turn_timeout_seconds(route, "retry", state) == 480
    assert worker.source_turn_timeout_seconds(
        replace(classmo, repository="example/CLASSMO"), "retry", state,
    ) == 480
    assert worker.source_turn_timeout_seconds(
        replace(classmo, branch="main"), "retry", state,
    ) == 480
    assert worker.source_turn_timeout_seconds(classmo, "retry", None) == 480
    assert worker.source_turn_timeout_seconds(
        classmo, "retry",
        replace(state, next_action=replace(action, source_task_id="DIFFERENT-TASK")),
    ) == 480
    assert worker.source_turn_timeout_seconds(
        classmo, "retry",
        replace(state, next_action=replace(action, type="DECISION_REQUIRED")),
    ) == 480


def test_extended_source_deadline_restores_timer_and_repair_cap_stays_480():
    previous = signal.getsignal(signal.SIGALRM)
    with pytest.raises(RepairPlanTimeout, match="bounded wall-clock deadline"):
        with extended_source_turn_deadline(0.02):
            time.sleep(0.3)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == previous
    with extended_source_turn_deadline(0.02):
        pass
    for bad in (0, -1, EXTENDED_SOURCE_TURN_TIMEOUT_SECONDS + 1):
        with pytest.raises(ValueError):
            with extended_source_turn_deadline(bad):
                pytest.fail("invalid extended deadline")
    with pytest.raises(ValueError):
        with repair_plan_deadline(REPAIR_PLAN_TIMEOUT_SECONDS + 1):
            pytest.fail("repair deadline was improperly expanded")
    assert worker.DirectWorkerTurnTimeout(960).seconds == 960
    assert worker.DirectWorkerTurnTimeout().seconds == 480


def test_active_timer_is_not_replaced():
    signal.setitimer(signal.ITIMER_REAL, 10)
    try:
        with pytest.raises(RuntimeError, match="active process timer"):
            with repair_plan_deadline(0.02):
                pytest.fail("nested deadline entered")
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 9
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
