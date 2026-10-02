from __future__ import annotations

import re
from dataclasses import dataclass

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class GuardError(ValueError):
    """Raised when a dispatch input violates the runner boundary."""


def require_repository(value: str) -> str:
    candidate = value.strip()
    if not _REPOSITORY_RE.fullmatch(candidate):
        raise GuardError("repository must use owner/name form")
    return candidate


def require_sha(value: str) -> str:
    candidate = value.strip().lower()
    if not _SHA_RE.fullmatch(candidate):
        raise GuardError("expected SHA must be exactly 40 lowercase hexadecimal characters")
    return candidate


@dataclass(frozen=True)
class ReadonlyPilotRequest:
    control_repository: str
    expected_sha: str

    @classmethod
    def validate(cls, control_repository: str, expected_sha: str) -> "ReadonlyPilotRequest":
        return cls(
            control_repository=require_repository(control_repository),
            expected_sha=require_sha(expected_sha),
        )
