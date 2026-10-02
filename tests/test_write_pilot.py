from pathlib import Path

import pytest

import wonjae_dispatcher_runner.write_pilot as pilot

SHA = "7a2688f14963de371c43bc67f729617b2d23de3c"


def test_parse_model_handoff_accepts_exact_payload() -> None:
    payload = (
        '{"ack":"AUTHORIZED_CONTROL_WRITE_PILOT",'
        '"observation":"GitHub exact-SHA evidence remains authoritative for current source state."}'
    )
    assert pilot.parse_model_handoff(payload) == pilot.MODEL_OBSERVATION


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "{}",
        '{"ack":"wrong","observation":"GitHub exact-SHA evidence remains authoritative for current source state."}',
        '{"ack":"AUTHORIZED_CONTROL_WRITE_PILOT","observation":"wrong"}',
    ],
)
def test_parse_model_handoff_rejects_invalid_payload(payload: str) -> None:
    with pytest.raises(pilot.WritePilotError):
        pilot.parse_model_handoff(payload)


def test_expected_document_binds_pilot_and_sha() -> None:
    text = pilot.expected_document(SHA)
    assert pilot.PILOT_ID in text
    assert SHA in text
    assert pilot.MODEL_OBSERVATION in text
    assert "No product TASK" in text


def test_validate_write_diff_accepts_exact_file(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / pilot.ALLOWED_PATH
    target.parent.mkdir(parents=True)
    target.write_text(pilot.expected_document(SHA), encoding="utf-8")
    monkeypatch.setattr(pilot, "changed_paths", lambda _repo: [pilot.ALLOWED_PATH.as_posix()])
    assert pilot.validate_write_diff(tmp_path, SHA) == target


def test_validate_write_diff_rejects_extra_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        pilot,
        "changed_paths",
        lambda _repo: [pilot.ALLOWED_PATH.as_posix(), "tasks/WAFL/TASK.md"],
    )
    with pytest.raises(pilot.WritePilotError):
        pilot.validate_write_diff(tmp_path, SHA)


def test_validate_write_diff_rejects_content_drift(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / pilot.ALLOWED_PATH
    target.parent.mkdir(parents=True)
    target.write_text("wrong\n", encoding="utf-8")
    monkeypatch.setattr(pilot, "changed_paths", lambda _repo: [pilot.ALLOWED_PATH.as_posix()])
    with pytest.raises(pilot.WritePilotError):
        pilot.validate_write_diff(tmp_path, SHA)
