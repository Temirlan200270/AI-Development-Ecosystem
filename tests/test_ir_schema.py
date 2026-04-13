"""IR v3: Pydantic ToolAction schema after IR v2 normalization."""
import pytest
from pydantic import ValidationError

from temir.core.action_ir import normalize_tool_action_envelope
from temir.core.ir_schema import (
    ToolAction,
    format_schema_repair_hint,
    validate_tool_steps_schema,
)


def test_tool_action_ok() -> None:
    m = ToolAction.model_validate(
        {"action": "write_file", "args": {"path": "a.py", "content": "x"}},
    )
    assert m.action == "write_file"


def test_tool_action_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        ToolAction.model_validate(
            {"action": "git_init", "args": {}, "oops": 1},
        )


def test_validate_steps_after_normalize() -> None:
    raw = normalize_tool_action_envelope(
        {"actions": [{"command": "echo hi"}]},
    )
    models = validate_tool_steps_schema(raw)
    assert len(models) == 1
    assert models[0].action == "execute_shell"


def test_format_schema_repair_hint_non_empty() -> None:
    try:
        ToolAction.model_validate({"action": "", "args": {}})
    except ValidationError as e:
        hint = format_schema_repair_hint(e)
        assert "SCHEMA_VALIDATION_FAILED" in hint
        assert "errors" in hint
    else:
        pytest.fail("expected ValidationError")
