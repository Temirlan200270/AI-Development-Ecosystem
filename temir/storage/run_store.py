"""Чтение списка ранов и событий из JSONL (replay, API, UI)."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from temir.storage.event_journal import get_journal_base, sanitize_run_id
from temir.web.event_schema import SCHEMA_VERSION

logger = logging.getLogger(__name__)


def list_run_ids() -> List[str]:
    base = get_journal_base()
    if not base.is_dir():
        return []
    names: List[str] = []
    for p in sorted(base.iterdir(), key=lambda x: x.name):
        if p.is_dir() and (p / "events.jsonl").is_file():
            names.append(p.name)
    return names


def load_run_events(run_id: str) -> List[Dict[str, Any]]:
    safe = sanitize_run_id(run_id)
    path = get_journal_base() / safe / "events.jsonl"
    if not path.is_file():
        return []
    events: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("run %s line %s: invalid JSON: %s", safe, line_no, e)
    return events


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def save_run_events_jsonl(
    run_id: str,
    events: List[Dict[str, Any]],
    *,
    overwrite: bool = False,
) -> Path:
    """Полная перезапись events.jsonl для run_id (branch / import)."""
    safe = sanitize_run_id(run_id)
    path = get_journal_base() / safe / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not overwrite:
        raise FileExistsError(f"journal already exists: {path}")
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return path


def branch_run_journal(
    parent_run_id: str,
    fork_seq: int,
    child_run_id: str,
) -> Path:
    """
    Копирует префикс журнала родителя (replay_cursor <= fork_seq) в новый run + маркер replay.branch_created.
    Детерминированный порядок — как в Replay Engine v2 (normalize_events_for_replay).
    """
    from temir.replay.state_machine import (
        normalize_events_for_replay,
        replay_cursor,
        strip_replay_private_fields,
    )

    parent_safe = sanitize_run_id(parent_run_id)
    child_safe = sanitize_run_id(child_run_id)
    if parent_safe == child_safe:
        raise ValueError("child_run_id must differ from parent_run_id")

    raw = load_run_events(parent_run_id)
    ordered = normalize_events_for_replay(raw)
    prefix: List[Dict[str, Any]] = []
    for ev in ordered:
        if replay_cursor(ev) <= fork_seq:
            prefix.append(strip_replay_private_fields(ev))

    max_seq = 0
    for ev in prefix:
        s = _safe_int(ev.get("seq"), 0)
        if s > max_seq:
            max_seq = s

    marker: Dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "run_id": child_safe,
        "pipeline_id": child_safe,
        "seq": max_seq + 1,
        "timestamp": time.time(),
        "schema_version": SCHEMA_VERSION,
        "topic": "replay.branch_created",
        "payload": {
            "parent_run_id": parent_safe,
            "fork_seq": int(fork_seq),
            "child_run_id": child_safe,
        },
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return save_run_events_jsonl(child_safe, prefix + [marker])
