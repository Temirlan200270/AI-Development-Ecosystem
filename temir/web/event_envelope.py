"""
Доменный контракт события пайплайна: трассировка + payload, независимый от wire JSON.
См. build_event_message_from_envelope — маппинг в envelope v1 (topic, payload, seq, …).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal, Optional


EventSource = Literal["orchestrator", "agent", "tool", "system"]


@dataclass(frozen=True)
class TraceContext:
    session_id: str
    task_id: Optional[str] = None
    step_id: Optional[str] = None


@dataclass(frozen=True)
class EventEnvelope:
    """Единый тип для публикации: event_type совпадает с зарегистрированным topic в event_schema."""

    event_type: str
    trace: TraceContext
    timestamp: float
    payload: dict[str, Any]
    source: EventSource = "orchestrator"


def envelope_now(
    event_type: str,
    trace: TraceContext,
    payload: dict[str, Any],
    *,
    source: EventSource = "orchestrator",
    ts: Optional[float] = None,
) -> EventEnvelope:
    """Фабрика с time.time() по умолчанию (оркестратор не дублирует вызовы time)."""
    return EventEnvelope(
        event_type=event_type,
        trace=trace,
        timestamp=time.time() if ts is None else ts,
        payload=payload,
        source=source,
    )
