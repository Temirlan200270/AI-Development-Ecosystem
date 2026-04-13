"""Контекст одного запуска пайплайна: run_id / pipeline_id и монотонный seq (для journal / replay)."""

from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass
class ActivePipelineRun:
    run_id: str
    pipeline_id: str
    seq: int = 0


_active: ContextVar[Optional[ActivePipelineRun]] = ContextVar(
    "temir_active_pipeline",
    default=None,
)
_seq_lock = asyncio.Lock()


def attach_pipeline_run(
    *,
    run_id: Optional[str] = None,
    pipeline_id: Optional[str] = None,
) -> str:
    """
    Начинает трассировку run (вызывать в начале execute_full_pipeline).
    По умолчанию run_id и pipeline_id совпадают (v1).
    """
    rid = run_id or str(uuid.uuid4())
    pid = pipeline_id or rid
    _active.set(ActivePipelineRun(run_id=rid, pipeline_id=pid))
    return rid


def detach_pipeline_run() -> None:
    """Сбрасывает контекст (finally после пайплайна)."""
    _active.set(None)


def current_run() -> Optional[ActivePipelineRun]:
    return _active.get()


async def next_seq_async() -> int:
    """Глобально упорядочивает события внутри процесса при параллельных task."""
    run = _active.get()
    if run is None:
        return 0
    async with _seq_lock:
        run.seq += 1
        return run.seq
