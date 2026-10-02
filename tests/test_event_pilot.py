from pathlib import Path

import wonjae_dispatcher_runner.event_pilot as pilot

SHA = "c83b31ae5da50fd0ed65e587d26dbae39d0705c2"


def test_expected_document_binds_sha() -> None:
    text = pilot.expected_document(SHA)
    assert SHA in text


def test_validate_diff_accepts_exact_file(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / pilot.ALLOWED_PATH
    target.parent.mkdir(parents=True)
    target.write_text(pilot.expected_document(SHA), encoding="utf-8")
    monkeypatch.setattr(pilot, "changed_paths", lambda _repo: [pilot.ALLOWED_PATH.as_posix()])
    assert pilot.validate_diff(tmp_path, SHA) == target
