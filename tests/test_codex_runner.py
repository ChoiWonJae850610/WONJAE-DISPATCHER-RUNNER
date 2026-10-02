import json

import pytest

from wonjae_dispatcher_runner.codex_runner import PilotError, parse_structured_sha

SHA = "7a2688f14963de371c43bc67f729617b2d23de3c"


def test_parse_structured_sha_accepts_exact_sha() -> None:
    assert parse_structured_sha(json.dumps({"sha": SHA})) == SHA


@pytest.mark.parametrize(
    "response",
    [
        "",
        "not-json",
        json.dumps({"sha": "7a2688f"}),
        json.dumps({"sha": SHA, "extra": "no"}),
        json.dumps({"sha": 123}),
        json.dumps(["sha", SHA]),
    ],
)
def test_parse_structured_sha_rejects_invalid_response(response: str) -> None:
    with pytest.raises(PilotError):
        parse_structured_sha(response)
