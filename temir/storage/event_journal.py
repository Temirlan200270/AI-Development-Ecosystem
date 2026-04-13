"""
Append-only JSONL event journal: один источник правды для replay / offline debug.
Путь по умолчанию: .andromeda/runs/{run_id}/events.jsonl
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_RUN_LOCKS: Dict[str, asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()

_SAFE_RUN_ID = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


def get_journal_base() -> Path:
    root = os.environ.get("TEMIR_EVENT_JOURNAL_DIR", "").strip()
    if root:
        return Path(root)
    return Path(".andromeda") / "runs"


def sanitize_run_id(run_id: str) -> str:
    """Имя каталога для run_id без path traversal."""
    if not run_id or not isinstance(run_id, str):
        return "local"
    rid = run_id.strip()
    if _SAFE_RUN_ID.match(rid):
        return rid
    # UUID и прочие строки: оставляем только безопасные символы
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", rid)[:128]
    return cleaned or "local"


async def _lock_for_run(safe_id: str) -> asyncio.Lock:
    async with _LOCKS_GUARD:
        if safe_id not in _RUN_LOCKS:
            _RUN_LOCKS[safe_id] = asyncio.Lock()
        return _RUN_LOCKS[safe_id]


def _write_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


async def append_envelope_async(envelope: Dict[str, Any]) -> None:
    """
    Добавляет одну строку JSONL (полный envelope, включая payload).
    Вызывать после успешной сборки сообщения в hub.publish.
    """
    run_id = envelope.get("run_id")
    safe = sanitize_run_id(str(run_id) if run_id is not None else "local")
    path = get_journal_base() / safe / "events.jsonl"
    line = json.dumps(envelope, ensure_ascii=False)
    lock = await _lock_for_run(safe)
    async with lock:
        await asyncio.to_thread(_write_line, path, line)
