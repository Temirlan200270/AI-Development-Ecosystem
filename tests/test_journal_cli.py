"""CLI journal: форматирование строк событий."""

from temir.journal_cli import format_event_line


def test_format_event_line_compact() -> None:
    s = format_event_line(
        {
            "seq": 3,
            "topic": "task.started",
            "run_id": "abc",
            "payload": {"task_id": "t1", "executor": "x"},
        },
        full=False,
    )
    assert "seq=3" in s
    assert "task.started" in s
    assert "task_id=" in s


def test_format_event_line_full_json() -> None:
    s = format_event_line({"topic": "x", "payload": {}}, full=True)
    assert '"topic"' in s
    assert "\n" in s
