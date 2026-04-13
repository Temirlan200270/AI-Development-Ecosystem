"""Level 2 closure: retry policy, level validation, execution gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from temir.core.execution_gate import ExecutionDecision, can_execute_tool_step
from temir.core.ir_v3 import StepMetaV3, StepV3
from temir.core.level_validation import LevelCompletionError, validate_level_completion
from temir.core.platform_context import PlatformContext
from temir.core.retry_policy import (
    ir_contract_error_retryable,
    preflight_violation_retryable,
)


def _step(step_id: str, action: str = "noop") -> StepV3:
    return StepV3(
        id=step_id,
        action=action,
        args={},
        meta=StepMetaV3(index=0, total=1),
    )


def test_ir_contract_retry_table() -> None:
    assert ir_contract_error_retryable("platform_mismatch") is True
    assert ir_contract_error_retryable("schema") is True
    assert ir_contract_error_retryable("unknown_action") is False
    assert ir_contract_error_retryable("capability_denied") is False
    assert ir_contract_error_retryable("graph") is False


def test_preflight_retry_table() -> None:
    assert preflight_violation_retryable("platform_mismatch") is True
    assert preflight_violation_retryable("schema") is True
    assert preflight_violation_retryable("unknown_action") is False
    assert preflight_violation_retryable("capability_denied") is False
    assert preflight_violation_retryable("graph") is False


def test_validate_level_completion_ok() -> None:
    steps = [_step("a"), _step("b")]
    records = [
        {"step_id": "a", "intent_sha256": "h1", "completed": True},
        {"step_id": "b", "intent_sha256": "h2", "completed": True},
    ]
    validate_level_completion(steps, records, idempotency_enabled=True)


def test_validate_level_completion_incomplete_step() -> None:
    steps = [_step("a")]
    records = [
        {"step_id": "a", "intent_sha256": "h1", "completed": False},
    ]
    with pytest.raises(LevelCompletionError, match="closed state"):
        validate_level_completion(steps, records, idempotency_enabled=True)


def test_validate_level_completion_duplicate_success_intent() -> None:
    steps = [_step("a")]
    records = [
        {"step_id": "a", "intent_sha256": "deadbeef", "completed": True},
        {"step_id": "a", "intent_sha256": "deadbeef", "completed": True},
    ]
    with pytest.raises(LevelCompletionError, match="duplicate"):
        validate_level_completion(steps, records, idempotency_enabled=True)


def test_idempotency_skip_in_gate() -> None:
    root = Path("/tmp/temir-test")
    platform = PlatformContext(os="linux", shell="bash")
    executed: set[tuple[str, str]] = {("task-1", "abc")}
    d = can_execute_tool_step(
        step_dict={"action": "list_directory", "args": {"path": "."}},
        task_id="task-1",
        project_root=root,
        registry=MagicMock(),
        platform=platform,
        allowed_capabilities=frozenset({"filesystem.read"}),
        executed_intents=executed,
        intent_sha256="abc",
        idempotency_enabled=True,
    )
    assert isinstance(d, ExecutionDecision)
    assert d.allowed is True
    assert d.skipped_idempotent is True
    assert d.reason == "idempotent_skip"
