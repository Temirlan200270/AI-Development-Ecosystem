"""
IR v3: schema-governed tool steps after IR v2 normalization.

Each step must match ToolAction exactly (no extra keys). Validation runs before execution preflight
so malformed structure triggers the contract repair loop, not path/shell policy.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ToolAction(BaseModel):
    """Strict executor contract for one tool invocation."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, description="Registered tool name")
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("args", mode="before")
    @classmethod
    def _coerce_args(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        raise TypeError("args must be an object (dict)")


def validate_tool_steps_schema(steps: list[dict[str, Any]]) -> list[ToolAction]:
    """Parse normalized dict steps into validated models; raises ValidationError on first bad step."""
    return [ToolAction.model_validate(step) for step in steps]


def format_schema_repair_hint(exc: ValidationError, *, max_errors: int = 12) -> str:
    """Human + machine-readable suffix for CONTRACT_REPAIR (Pydantic v2)."""
    errs = exc.errors()[:max_errors]
    detail = json.dumps(errs, indent=2, default=str)
    schema_hint = (
        'Each step must be exactly: {"action": "<registered_name>", "args": {...}} '
        "with no other keys. For batches use IR envelope {\"actions\": [ ... ]}."
    )
    return f"SCHEMA_VALIDATION_FAILED\n{schema_hint}\nerrors:\n{detail}"


def tool_action_json_schema() -> dict[str, Any]:
    """For prompts / docs: JSON Schema of a single ToolAction object."""
    return ToolAction.model_json_schema()
