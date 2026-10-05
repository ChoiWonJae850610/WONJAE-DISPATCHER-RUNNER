import signal
import time

import pytest

from wonjae_dispatcher_runner.repair_timeout import (
    REPAIR_PLAN_TIMEOUT_SECONDS,
    RepairPlanTimeout,
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


def test_active_timer_is_not_replaced():
    signal.setitimer(signal.ITIMER_REAL, 10)
    try:
        with pytest.raises(RuntimeError, match="active process timer"):
            with repair_plan_deadline(0.02):
                pytest.fail("nested deadline entered")
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 9
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
