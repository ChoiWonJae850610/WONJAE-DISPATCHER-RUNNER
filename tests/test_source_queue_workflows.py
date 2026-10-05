from pathlib import Path

PRODUCT_WORKFLOWS = {
    "CLASSMO": ".github/workflows/classmo-product-pilot.yml",
    "WAFL": ".github/workflows/wafl-product-v2.yml",
    "ESC": ".github/workflows/esc-product-v2.yml",
    "MUVEL": ".github/workflows/muvel-product-v2.yml",
}


def test_source_workflows_are_queue_dispatched_not_issue_opened() -> None:
    for project, path in PRODUCT_WORKFLOWS.items():
        text = Path(path).read_text(encoding="utf-8")
        assert "workflow_dispatch:" in text
        assert "issues:" not in text.split("permissions:", 1)[0]
        assert "wake_issue_number:" in text
        assert "task_id:" in text
        assert "control_sha:" in text
        assert "source_sha:" in text
        assert "## Dispatcher v2 source queue evidence" in text
        assert "- queue_state: `DISPATCHED`" in text
        assert "source_queue.py resolve-control" in text
        assert "source_queue.py record-completed" in text
        assert f"[PRODUCT-WAKE][DISPATCHER-V2] {project}" in text


def test_queue_controller_is_event_driven_and_has_no_poll_loop() -> None:
    text = Path(".github/workflows/source-queue-controller.yml").read_text(encoding="utf-8")

    assert "issues:" in text
    assert "workflow_run:" in text
    assert "actions: write" in text
    assert "cancel-in-progress: false" in text
    assert "source_queue.py intake" in text
    assert "source_queue.py advance" in text
    assert "[PRODUCT-QUEUE][DISPATCHER-V2]" in text
    assert "CONTROL_READ_TOKEN" in text
    assert "sleep " not in text
    for project in PRODUCT_WORKFLOWS:
        assert f"- {project} Dispatcher v2" in text


def test_terminal_guard_resolves_queue_issue_and_exact_source_authority() -> None:
    text = Path(".github/workflows/product-terminal-guard.yml").read_text(encoding="utf-8")

    script = Path("scripts/source_terminal.py").read_text(encoding="utf-8")
    core = Path("src/wonjae_dispatcher_runner/source_terminal.py").read_text(encoding="utf-8")
    assert "source_terminal.py select" in text
    assert "queue.locate_issue(args)" in script
    assert "queue.resolve_control(" in script
    assert "allow_closed=True" in script
    assert '"source_base_sha": source' in core
    assert "terminalize_exact_pr" in core


def test_source_validation_polling_remains_inside_runner_only() -> None:
    for path in PRODUCT_WORKFLOWS.values():
        text = Path(path).read_text(encoding="utf-8")
        assert "gh run watch" in text
        assert "sleep 10" in text

    controller = Path(".github/workflows/source-queue-controller.yml").read_text(
        encoding="utf-8"
    )
    assert "gh run watch" not in controller
    assert "sleep " not in controller

