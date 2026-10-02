import pytest

from wonjae_dispatcher_runner.codex_runner import (
    PilotError,
    extract_unique_reported_sha,
)


SHA = "7a2688f14963de371c43bc67f729617b2d23de3c"


@pytest.mark.parametrize(
    "response",
    [
        SHA,
        f"`{SHA}`",
        f"HEAD is {SHA}.",
        f"\n{SHA}\n",
        SHA.upper(),
    ],
)
def test_extract_unique_reported_sha_accepts_one_full_sha(response: str) -> None:
    assert extract_unique_reported_sha(response) == SHA


@pytest.mark.parametrize(
    "response",
    [
        "",
        "HEAD is unknown",
        "7a2688f",
        f"{SHA} and {'b' * 40}",
    ],
)
def test_extract_unique_reported_sha_rejects_missing_or_ambiguous_sha(response: str) -> None:
    with pytest.raises(PilotError):
        extract_unique_reported_sha(response)
