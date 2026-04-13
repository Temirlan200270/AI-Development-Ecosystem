"""
Event Schema v1: envelope, реестр топиков, валидация payload при publish.
Режим strict: TEMIR_EVENT_SCHEMA_STRICT=1 — неизвестный топик или пропуск поля → исключение.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, Optional

from temir.web.event_envelope import EventEnvelope
from temir.web.run_telemetry import current_run, next_seq_async

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

# Обязательные ключи в payload (не в envelope). Пустой frozenset = топик без требований к полям.
TOPIC_PAYLOAD_REQUIREMENTS: Dict[str, FrozenSet[str]] = {
    "pipeline.started": frozenset({"user_request", "output_dir"}),
    "pipeline.plan_ready": frozenset({"task_count", "task_ids"}),
    "pipeline.completed": frozenset(
        {"success", "total_cost_usd", "completed", "failed"},
    ),
    "pipeline.failed": frozenset({"phase", "error"}),
    "task.created": frozenset({"task_id", "executor", "dependencies"}),
    "task.started": frozenset({"task_id", "executor"}),
    "task.completed": frozenset({"task_id", "executor"}),
    "task.failed": frozenset({"task_id", "executor", "error"}),
    "task.skipped": frozenset({"task_id", "reason"}),
    "task.cache_hit": frozenset({"task_id", "executor", "action"}),
    "patch.proposed": frozenset({"task_id", "summary"}),
    "patch.applied": frozenset({"task_id", "action"}),
    "patch.failed": frozenset({"task_id", "action", "detail"}),
    "cost.tick": frozenset({"usd_delta", "usd_total", "source"}),
    "decision.selected": frozenset({"task_id", "decision", "reason"}),
    "decision.alternatives": frozenset({"task_id", "alternatives"}),
    "agent.started": frozenset({"task_id", "role"}),
    "agent.log": frozenset({"task_id", "message"}),
    "agent.finished": frozenset({"task_id", "role", "success"}),
    "agent.event": frozenset({"message"}),
    "client.message": frozenset({"data"}),
    "parse.error": frozenset({"raw"}),
    "llm.requested": frozenset(
        {"provider", "prompt_length", "role_hint", "task_id"},
    ),
    "llm.completed": frozenset(
        {
            "success",
            "provider",
            "latency_ms",
            "billing_model",
            "input_tokens",
            "output_tokens",
            "error_preview",
        },
    ),
    "replay.branch_created": frozenset(
        {"parent_run_id", "fork_seq", "child_run_id"},
    ),
    "decision.strategy.selected": frozenset(
        {"task_id", "strategy", "reason", "task_ids"},
    ),
    "tool.execution.started": frozenset({"task_id", "tool", "arg_keys"}),
    "tool.preflight.failed": frozenset(
        {"task_id", "code", "message", "repair_hint"},
    ),
    "tool.schema.failed": frozenset({"task_id", "error_summary"}),
    "tool.ir.normalized": frozenset(
        {
            "task_id",
            "step_count",
            "source",
            "execution_mode",
            "ir_generation",
            "platform_os",
            "platform_shell",
        },
    ),
    "tool.ir.batch_flattened": frozenset({"task_id", "step_count"}),
    "tool.ir.rejected": frozenset({"task_id", "code", "message"}),
    "execution.level.started": frozenset(
        {
            "task_id",
            "level_index",
            "step_ids",
            "mode",
            "parallel_eligible",
            "platform_os",
            "platform_shell",
        },
    ),
    "audit.step.record": frozenset(
        {
            "task_id",
            "step_id",
            "step_seq",
            "action",
            "level_index",
            "intent_sha256",
            "capabilities",
            "success",
        },
    ),
    "audit.capability.denied": frozenset(
        {"task_id", "action", "code", "missing", "message"},
    ),
    "evaluation.test.run": frozenset({"task_id", "path_or_command"}),
    "decision.execution.fallback": frozenset({"task_id", "reason"}),
    "reflection.loop.triggered": frozenset({"task_id", "phase"}),
}


def schema_strict_mode() -> bool:
    v = (os.environ.get("TEMIR_EVENT_SCHEMA_STRICT") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def validate_payload(topic: str, payload: Dict[str, Any]) -> None:
    required = TOPIC_PAYLOAD_REQUIREMENTS.get(topic)
    if required is None:
        msg = f"event schema v1: unknown topic {topic!r}"
        if schema_strict_mode():
            raise ValueError(msg)
        logger.warning("%s (soft mode)", msg)
        return
    missing = sorted(k for k in required if k not in payload)
    if not missing:
        return
    msg = f"event schema v1: topic {topic!r} missing payload keys: {missing}"
    if schema_strict_mode():
        raise ValueError(msg)
    logger.warning("%s (soft mode)", msg)


async def build_event_message(
    topic: str,
    payload: Optional[Dict[str, Any]],
    *,
    session_id: str = "default",
) -> Dict[str, Any]:
    """Собирает сообщение для WebSocket: envelope v1 + совместимость с прежним полем ts."""
    body = dict(payload or {})
    validate_payload(topic, body)

    run = current_run()
    run_id = run.run_id if run else "local"
    pipeline_id = run.pipeline_id if run else "local"
    seq = await next_seq_async()

    ts_unix = time.time()
    ts_iso = datetime.now(timezone.utc).isoformat()

    envelope: Dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "run_id": run_id,
        "pipeline_id": pipeline_id,
        "seq": seq,
        "timestamp": ts_unix,
        "schema_version": SCHEMA_VERSION,
        "topic": topic,
        "payload": body,
        "session_id": session_id,
        "ts": ts_iso,
    }
    return envelope


async def build_event_message_from_envelope(
    env: EventEnvelope,
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Wire envelope v1 из доменного EventEnvelope.
    timestamp/ts берутся из env.timestamp (детерминированный replay относительно события).
    Доп. поля: trace_context, event_source — не ломают validate_payload (он только по payload).
    """
    body = dict(env.payload)
    validate_payload(env.event_type, body)
    tid_trace = env.trace.task_id
    tid_payload = body.get("task_id")
    if tid_trace is not None and tid_payload is not None and str(tid_trace) != str(
        tid_payload,
    ):
        logger.warning(
            "trace.task_id %r != payload.task_id %r for topic %r",
            tid_trace,
            tid_payload,
            env.event_type,
        )

    run = current_run()
    run_id = run.run_id if run else "local"
    pipeline_id = run.pipeline_id if run else "local"
    seq = await next_seq_async()
    sid = session_id if session_id is not None else env.trace.session_id
    ts_iso = datetime.fromtimestamp(env.timestamp, tz=timezone.utc).isoformat()

    return {
        "event_id": str(uuid.uuid4()),
        "run_id": run_id,
        "pipeline_id": pipeline_id,
        "seq": seq,
        "timestamp": env.timestamp,
        "schema_version": SCHEMA_VERSION,
        "topic": env.event_type,
        "payload": body,
        "session_id": sid,
        "ts": ts_iso,
        "trace_context": {
            "session_id": env.trace.session_id,
            "task_id": env.trace.task_id,
            "step_id": env.trace.step_id,
        },
        "event_source": env.source,
    }
