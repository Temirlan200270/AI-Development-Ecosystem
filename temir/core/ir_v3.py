"""
IR v3 — production execution contract: LLM JSON → typed ExecutionPlanV3 → executor.

Optional DAG: depends_on (step ids), execution_mode sequential | dag.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from temir.core.action_ir import ActionIRNormalizeError, normalize_tool_action_envelope
from temir.core.execution_graph import ExecutionGraphError, validate_acyclic
from temir.core.ir_schema import format_schema_repair_hint, validate_tool_steps_schema
from temir.core.platform_context import (
    PlatformContext,
    execute_shell_platform_mismatch_reason,
    platform_repair_hint,
    resolve_platform_context,
)
from temir.core.tool_registry import ToolRegistry


class IRV3ContractError(ValueError):
    """Compiled plan violates IR v3 contract (normalize, schema, registry, graph, or batch limits)."""

    def __init__(self, message: str, *, code: str = "contract") -> None:
        self.code = code
        super().__init__(message)


class StepMetaSource(str, Enum):
    LLM = "llm"
    CACHE = "cache"
    SYSTEM = "system"


class ExecutionMode(str, Enum):
    """sequential: one step after another. dag: honor depends_on; empty deps → implicit linear chain."""

    SEQUENTIAL = "sequential"
    DAG = "dag"


# Человекочитаемые алиасы из спек / LLM → реальные имена AgentTools
_ACTION_CANONICAL_ALIASES: dict[str, str] = {
    "delete_file": "remove_path",
    "patch_file": "smart_patch",
    "patch": "smart_patch",
}


class StepMetaV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    total: int = Field(ge=1)
    retryable: bool = True
    max_retries: int = Field(default=0, ge=0, le=8, description="Reserved for edge-level retry policy")
    timeout_ms: int | None = None
    unsafe: bool = False
    source: StepMetaSource = StepMetaSource.LLM


class StepV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    meta: StepMetaV3


class ExecutionPlanV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    ir_generation: str = Field(default="v4", description="IR contract generation (v3/v4)")
    execution_mode: ExecutionMode = ExecutionMode.SEQUENTIAL
    steps: list[StepV3] = Field(min_length=1)


def _apply_canonical_action_names(step: dict[str, Any]) -> dict[str, Any]:
    act = str(step["action"])
    mapped = _ACTION_CANONICAL_ALIASES.get(act, act)
    return {"action": mapped, "args": dict(step.get("args") or {})}


def _max_batch_steps() -> int:
    raw = (os.environ.get("TEMIR_IR_MAX_BATCH_STEPS") or "64").strip()
    try:
        n = int(raw)
    except ValueError:
        return 64
    return max(1, min(n, 500))


def _execution_mode_from_raw(raw: dict[str, Any]) -> ExecutionMode:
    v = (
        raw.get("execution_mode")
        or os.environ.get("TEMIR_IR_EXECUTION_MODE")
        or "sequential"
    )
    s = str(v).strip().lower()
    if s in ("dag", "graph", "parallel"):
        return ExecutionMode.DAG
    return ExecutionMode.SEQUENTIAL


def _deps_from_loose_row(row: dict[str, Any]) -> list[str]:
    d = row.get("depends_on")
    if isinstance(d, list):
        return [str(x) for x in d]
    return []


def compile_llm_json_to_execution_plan_v3(
    raw: dict[str, Any],
    *,
    task_id: str,
    registry: ToolRegistry,
    meta_source: StepMetaSource = StepMetaSource.LLM,
    allow_text_shell: bool | None = None,
    platform: PlatformContext | None = None,
) -> ExecutionPlanV3:
    """
    Single entry: raw LLM action_json → validated ExecutionPlanV3.

    allow_text_shell: forwarded to IR v2; None = env TEMIR_IR_ALLOW_TEXT_SHELL.
    """
    mode = _execution_mode_from_raw(raw)

    try:
        loose_steps = normalize_tool_action_envelope(
            raw,
            allow_text_shell=allow_text_shell,
        )
    except ActionIRNormalizeError as e:
        raise IRV3ContractError(str(e), code="normalize") from e

    limit = _max_batch_steps()
    if len(loose_steps) > limit:
        raise IRV3ContractError(
            f"Batch too large ({len(loose_steps)} steps, max {limit}). "
            f"Set TEMIR_IR_MAX_BATCH_STEPS to raise limit.",
            code="batch_limit",
        )

    deps_meta = [_deps_from_loose_row(s) for s in loose_steps]
    canon = [_apply_canonical_action_names(s) for s in loose_steps]

    try:
        validated = validate_tool_steps_schema(canon)
    except ValidationError as e:
        raise IRV3ContractError(
            format_schema_repair_hint(e),
            code="schema",
        ) from e

    allowed = registry.names
    for i, tm in enumerate(validated):
        if tm.action not in allowed:
            preview = ", ".join(sorted(allowed))
            raise IRV3ContractError(
                f"Step {i}: action {tm.action!r} is not in the tool registry. Allowed: {preview}",
                code="unknown_action",
            )

    plat = platform if platform is not None else resolve_platform_context(None)
    for i, tm in enumerate(validated):
        if tm.action != "execute_shell":
            continue
        cmd = tm.args.get("command")
        if not isinstance(cmd, str):
            continue
        # SSOT: temir.core.platform_context.execute_shell_platform_mismatch_reason
        reason = execute_shell_platform_mismatch_reason(cmd, plat)
        if reason:
            raise IRV3ContractError(
                f"Step {i}: platform_mismatch — {reason}. {platform_repair_hint(plat)}",
                code="platform_mismatch",
            )

    n = len(validated)
    steps: list[StepV3] = []
    for i, tm in enumerate(validated):
        sid = f"{task_id}:step:{i}"
        meta = StepMetaV3(
            index=i,
            total=n,
            retryable=True,
            max_retries=0,
            timeout_ms=None,
            unsafe=tm.action == "execute_shell",
            source=meta_source,
        )
        steps.append(
            StepV3(
                id=sid,
                action=tm.action,
                args=dict(tm.args),
                depends_on=list(deps_meta[i]),
                meta=meta,
            ),
        )

    # DAG + пустые зависимости: по умолчанию вести себя как цепочка (как раньше).
    # TEMIR_IR_DAG_IMPLICIT_LINEAR=0 — все корневые шаги в одном уровне (fork → parallel gather при safe).
    implicit_linear = (
        os.environ.get("TEMIR_IR_DAG_IMPLICIT_LINEAR") or "1"
    ).strip().lower() in ("1", "true", "yes", "on")
    if (
        mode == ExecutionMode.DAG
        and n > 1
        and implicit_linear
        and all(len(s.depends_on) == 0 for s in steps)
    ):
        rebuilt: list[StepV3] = []
        for i, s in enumerate(steps):
            d_on = [steps[i - 1].id] if i > 0 else []
            rebuilt.append(s.model_copy(update={"depends_on": d_on}))
        steps = rebuilt

    try:
        validate_acyclic(steps)
    except ExecutionGraphError as e:
        raise IRV3ContractError(str(e), code="graph") from e

    return ExecutionPlanV3(
        task_id=task_id,
        execution_mode=mode,
        steps=steps,
    )


def plan_to_executor_dicts(plan: ExecutionPlanV3) -> list[dict[str, Any]]:
    """Strip to runtime {action, args} for preflight + asyncio.to_thread."""
    return [{"action": s.action, "args": dict(s.args)} for s in plan.steps]
