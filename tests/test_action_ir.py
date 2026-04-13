"""Tests for Action IR normalization (single vs batch envelopes)."""
import pytest

from temir.core.action_ir import ActionIRNormalizeError, normalize_tool_action_envelope


def test_single_envelope() -> None:
    raw = {"action": "write_file", "args": {"path": "a.py", "content": "x"}}
    steps = normalize_tool_action_envelope(raw)
    assert len(steps) == 1
    assert steps[0]["action"] == "write_file"
    assert steps[0]["args"] == {"path": "a.py", "content": "x"}


def test_batch_envelope() -> None:
    raw = {
        "actions": [
            {"action": "write_file", "args": {"path": "a", "content": ""}},
            {"action": "write_file", "args": {"path": "b", "content": "z"}},
        ],
    }
    steps = normalize_tool_action_envelope(raw)
    assert len(steps) == 2
    assert steps[0]["args"]["path"] == "a"
    assert steps[1]["args"]["path"] == "b"


def test_batch_coerces_non_dict_args() -> None:
    raw = {"actions": [{"action": "git_init", "args": "bad"}]}
    steps = normalize_tool_action_envelope(raw)
    assert steps[0]["args"] == {}


def test_rejects_empty_actions() -> None:
    with pytest.raises(ActionIRNormalizeError):
        normalize_tool_action_envelope({"actions": []})


def test_rejects_missing_action() -> None:
    with pytest.raises(ActionIRNormalizeError):
        normalize_tool_action_envelope({"foo": 1})


def test_rejects_non_dict() -> None:
    with pytest.raises(ActionIRNormalizeError):
        normalize_tool_action_envelope([])  # type: ignore[arg-type]


def test_mercury_command_string() -> None:
    steps = normalize_tool_action_envelope({"command": "dir /b /s"})
    assert len(steps) == 1
    assert steps[0]["action"] == "execute_shell"
    assert steps[0]["args"]["command"] == "dir /b /s"


def test_mercury_cmd_list() -> None:
    steps = normalize_tool_action_envelope({"cmd": ["python", "-m", "pytest", "-q"]})
    assert steps[0]["action"] == "execute_shell"
    assert "pytest" in steps[0]["args"]["command"]


def test_mercury_tool_plus_arguments() -> None:
    steps = normalize_tool_action_envelope(
        {
            "tool": "write_file",
            "arguments": {"path": "x.txt", "content": "hi"},
        },
    )
    assert steps[0]["action"] == "write_file"
    assert steps[0]["args"]["path"] == "x.txt"


def test_mercury_shell_alias_action() -> None:
    steps = normalize_tool_action_envelope(
        {"action": "shell", "args": {"cmd": "echo ok"}},
    )
    assert steps[0]["action"] == "execute_shell"
    assert steps[0]["args"]["command"] == "echo ok"


def test_actions_batch_mixed_native_and_loose() -> None:
    steps = normalize_tool_action_envelope(
        {
            "actions": [
                {"action": "git_init", "args": {}},
                {"command": "git status"},
            ],
        },
    )
    assert len(steps) == 2
    assert steps[0]["action"] == "git_init"
    assert steps[1]["action"] == "execute_shell"


def test_text_only_shell_snippet_requires_flag() -> None:
    with pytest.raises(ActionIRNormalizeError):
        normalize_tool_action_envelope({"text": "ruff check ."}, allow_text_shell=False)

    steps = normalize_tool_action_envelope(
        {"text": "ruff check ."},
        allow_text_shell=True,
    )
    assert steps[0]["action"] == "execute_shell"
    assert steps[0]["args"]["command"] == "ruff check ."
