"""
Минимальный fold событий в ExecutionState (параллельно императивным обновлениям оркестратора).
Использует только topic + payload, как в JSONL / wire envelope.
"""

from __future__ import annotations

from typing import Any, Mapping

from temir.core.models import ExecutionState
from temir.web.event_envelope import EventEnvelope


def reduce_execution_state(
    state: ExecutionState,
    topic: str,
    payload: Mapping[str, Any],
) -> ExecutionState:
    pl = dict(payload)
    if topic == "cost.tick":
        if "usd_total" in pl and isinstance(pl.get("usd_total"), (int, float)):
            total = float(pl["usd_total"])
        elif "usd_delta" in pl and isinstance(pl.get("usd_delta"), (int, float)):
            total = state.total_cost + float(pl["usd_delta"])
        else:
            return state
        return state.model_copy(update={"total_cost": total})
    if topic == "task.completed":
        tid = str(pl.get("task_id") or "")
        if not tid or tid in state.completed_tasks:
            return state
        completed = list(state.completed_tasks) + [tid]
        return state.model_copy(
            update={
                "completed_tasks": completed,
                "successful_tasks": len(completed),
            },
        )
    if topic == "task.failed":
        tid = str(pl.get("task_id") or "")
        if not tid or tid in state.failed_tasks:
            return state
        failed = list(state.failed_tasks) + [tid]
        return state.model_copy(
            update={
                "failed_tasks": failed,
                "failed_tasks_count": state.failed_tasks_count + 1,
            },
        )
    return state


def reduce_execution_state_from_wire(
    state: ExecutionState,
    envelope: Mapping[str, Any],
) -> ExecutionState:
    topic = str(envelope.get("topic") or "")
    raw = envelope.get("payload")
    payload = raw if isinstance(raw, dict) else {}
    return reduce_execution_state(state, topic, payload)


def reduce_execution_state_from_domain(
    state: ExecutionState,
    event: EventEnvelope,
) -> ExecutionState:
    return reduce_execution_state(state, event.event_type, event.payload)


class ExecutionStateReducer:
    """Тонкая обёртка для единообразия с event-sourced стилем (state' = reduce(state, event))."""

    def reduce_wire(self, state: ExecutionState, wire: Mapping[str, Any]) -> ExecutionState:
        return reduce_execution_state_from_wire(state, wire)

    def reduce_domain(self, state: ExecutionState, event: EventEnvelope) -> ExecutionState:
        return reduce_execution_state_from_domain(state, event)
