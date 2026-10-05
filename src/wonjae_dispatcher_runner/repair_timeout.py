"""Wall-clock deadline for a read-only Codex repair turn on the Linux runner."""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager

REPAIR_PLAN_TIMEOUT_SECONDS = 480


class RepairPlanTimeout(RuntimeError):
    pass


@contextmanager
def repair_plan_deadline(seconds: float = REPAIR_PLAN_TIMEOUT_SECONDS) -> Iterator[None]:
    # Fail closed rather than silently running without the deadline on another host/thread.
    if (not hasattr(signal, "setitimer")
            or threading.current_thread() is not threading.main_thread()):
        raise RuntimeError("repair plan deadline requires the Linux runner main thread")
    if seconds <= 0 or seconds > REPAIR_PLAN_TIMEOUT_SECONDS:
        raise ValueError("repair plan deadline must be positive and at most 480 seconds")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError("repair plan deadline may not replace an active process timer")
    previous = signal.getsignal(signal.SIGALRM)

    def timeout(signum: int, frame: object) -> None:
        raise RepairPlanTimeout("read-only repair plan exceeded its 480-second wall-clock deadline")

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
