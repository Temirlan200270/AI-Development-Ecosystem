"""Доменный EventEnvelope и мост в wire envelope."""

from __future__ import annotations

import asyncio

import pytest

from temir.core.execution_state_reducer import (
    reduce_execution_state,
    reduce_execution_state_from_wire,
)
from temir.core.models import ExecutionState
from temir.web.event_envelope import EventEnvelope, TraceContext, envelope_now
from temir.web.event_schema import SCHEMA_VERSION, build_event_message_from_envelope
from temir.web.run_telemetry import attach_pipeline_run, detach_pipeline_run


def test_envelope_now_frozen() -> None:
    env = envelope_now(
        "task.started",
        TraceContext(session_id="s1", task_id="t1"),
        {"task_id": "t1", "executor": "x"},
    )
    assert env.event_type == "task.started"
    assert env.trace.task_id == "t1"
    assert env.source == "orchestrator"


def test_build_from_envelope_trace_on_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEMIR_EVENT_SCHEMA_STRICT", raising=False)

    async def _run() -> None:
        attach_pipeline_run(run_id="run-a")
        try:
            env = EventEnvelope(
                event_type="cost.tick",
                trace=TraceContext(session_id="run-a"),
                timestamp=1700000000.5,
                payload={
                    "usd_delta": 0.01,
                    "usd_total": 0.5,
                    "source": "test",
                },
                source="system",
            )
            wire = await build_event_message_from_envelope(env)
            assert wire["topic"] == "cost.tick"
            assert wire["timestamp"] == 1700000000.5
            assert wire["event_source"] == "system"
            assert wire["trace_context"]["session_id"] == "run-a"
            assert wire["payload"]["usd_total"] == 0.5
            assert wire["schema_version"] == SCHEMA_VERSION
        finally:
            detach_pipeline_run()

    asyncio.run(_run())


def test_reduce_cost_and_tasks() -> None:
    s0 = ExecutionState()
    s1 = reduce_execution_state(
        s0,
        "cost.tick",
        {"usd_delta": 0.1, "usd_total": 0.1, "source": "t"},
    )
    assert s1.total_cost == pytest.approx(0.1)
    s2 = reduce_execution_state(
        s1,
        "task.completed",
        {"task_id": "a", "executor": "e"},
    )
    assert "a" in s2.completed_tasks
    assert s2.successful_tasks == 1
    s3 = reduce_execution_state(
        s2,
        "task.failed",
        {"task_id": "b", "executor": "e", "error": "x"},
    )
    assert "b" in s3.failed_tasks
    assert s3.failed_tasks_count == 1


def test_reduce_from_wire() -> None:
    wire = {
        "topic": "cost.tick",
        "payload": {"usd_total": 2.0, "usd_delta": 0.0, "source": "j"},
    }
    s = reduce_execution_state_from_wire(ExecutionState(), wire)
    assert s.total_cost == pytest.approx(2.0)
