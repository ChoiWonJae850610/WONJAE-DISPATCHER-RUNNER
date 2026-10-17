"""Bounded wall-clock deadlines for Codex repair and source turns."""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager

REPAIR_PLAN_TIMEOUT_SECONDS = 480
EXTENDED_SOURCE_TURN_TIMEOUT_SECONDS = 960


class RepairPlanTimeout(RuntimeError):
    pass


@contextmanager
def _bounded_deadline(
    seconds: float, maximum: float, description: str,
) -> Iterator[None]:
    # Fail closed on unsupported hosts or threads; never override another timer.
    if (not hasattr(signal, "setitimer")
            or threading.current_thread() is not threading.main_thread()):
        raise RuntimeError("Codex deadline requires the Linux runner main thread")
    if seconds <= 0 or seconds > maximum:
        raise ValueError(f"Codex deadline must be positive and at most {maximum:g} seconds")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError("Codex deadline may not replace an active process timer")
    previous = signal.getsignal(signal.SIGALRM)

    def timeout(signum: int, frame: object) -> None:
        raise RepairPlanTimeout(description)

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextmanager
def repair_plan_deadline(seconds: float = REPAIR_PLAN_TIMEOUT_SECONDS) -> Iterator[None]:
    """Keep every read-only repair plan capped at its original 480 seconds."""
    with _bounded_deadline(
        seconds, REPAIR_PLAN_TIMEOUT_SECONDS,
        "read-only repair plan exceeded its 480-second wall-clock deadline",
    ):
        yield


@contextmanager
def extended_source_turn_deadline(
    seconds: float = EXTENDED_SOURCE_TURN_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Only the separately gated exact source retry calls this bounded extension."""
    with _bounded_deadline(
        seconds, EXTENDED_SOURCE_TURN_TIMEOUT_SECONDS,
        "direct source turn exceeded its bounded wall-clock deadline",
    ):
        yield
