import pytest

from wonjae_dispatcher_runner.guards import GuardError, ReadonlyPilotRequest, require_repository


def test_readonly_request_accepts_exact_inputs() -> None:
    sha = "a" * 40
    request = ReadonlyPilotRequest.validate("owner/control", sha)
    assert request.control_repository == "owner/control"
    assert request.expected_sha == sha


@pytest.mark.parametrize(
    "value",
    ["owner", "/repo", "owner/", "https://github.com/owner/repo", "owner/repo/extra"],
)
def test_repository_guard_rejects_non_owner_name(value: str) -> None:
    with pytest.raises(GuardError):
        require_repository(value)


def test_sha_guard_rejects_short_value() -> None:
    with pytest.raises(GuardError):
        ReadonlyPilotRequest.validate("owner/control", "abc123")
