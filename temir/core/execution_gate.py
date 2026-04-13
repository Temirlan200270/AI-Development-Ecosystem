"""
Единая точка допуска шага к исполнению (Level 2): capabilities + preflight + idempotency.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, FrozenSet, Mapping

from temir.core.action_preflight import ActionPreflightViolation, preflight_tool_steps
from temir.core.capabilities import CapabilityDeniedError, capabilities_required_for_action
from temir.core.platform_context import PlatformContext
from temir.core.retry_policy import preflight_violation_retryable


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str
    retryable: bool
    terminal: bool
    skipped_idempotent: bool = False


def can_execute_tool_step(
    *,
    step_dict: Mapping[str, Any],
    task_id: str,
    project_root: Path,
    registry: Any,
    platform: PlatformContext,
    allowed_capabilities: FrozenSet[str],
    executed_intents: set[tuple[str, str]],
    intent_sha256: str,
    idempotency_enabled: bool,
) -> ExecutionDecision:
    """
    Единственный gate перед asyncio.to_thread(tool).

    executed_intents: пары (task_id, intent_sha256) после успешного шага.
    """
    if idempotency_enabled and (task_id, intent_sha256) in executed_intents:
        return ExecutionDecision(
            allowed=True,
            reason="idempotent_skip",
            retryable=False,
            terminal=False,
            skipped_idempotent=True,
        )

    action = str(step_dict.get("action") or "")
    args = step_dict.get("args")
    if not isinstance(args, dict):
        args = {}

    try:
        need = capabilities_required_for_action(action)
        denied = need - allowed_capabilities
        if denied:
            return ExecutionDecision(
                allowed=False,
                reason=f"capability_denied missing={sorted(denied)}",
                retryable=False,
                terminal=True,
            )
    except CapabilityDeniedError as e:
        return ExecutionDecision(
            allowed=False,
            reason=str(e),
            retryable=False,
            terminal=True,
        )

    try:
        preflight_tool_steps(
            [{"action": action, "args": dict(args)}],
            project_root=project_root,
            registry=registry,
            platform=platform,
        )
    except ActionPreflightViolation as e:
        r = preflight_violation_retryable(e.code)
        return ExecutionDecision(
            allowed=False,
            reason=f"{e.code}: {e}",
            retryable=r,
            terminal=not r,
        )

    return ExecutionDecision(
        allowed=True,
        reason="ok",
        retryable=True,
        terminal=False,
    )


def register_successful_intent(
    executed_intents: set[tuple[str, str]],
    *,
    task_id: str,
    intent_sha256: str,
) -> None:
    executed_intents.add((task_id, intent_sha256))
