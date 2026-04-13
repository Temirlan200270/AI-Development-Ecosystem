"""
IR v4 — deterministic step audit: canonical JSON + SHA-256 for replay / verification.

Hash covers task_id, step_id, step_seq, action, args (sorted), level_index, capabilities.
Do not include wall clock or non-deterministic fields.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def _canonical_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def compute_step_intent_sha256(
    *,
    task_id: str,
    step_id: str,
    step_seq: int,
    action: str,
    args: dict[str, Any],
    level_index: int,
    capabilities: Iterable[str],
) -> tuple[dict[str, Any], str]:
    record: dict[str, Any] = {
        "task_id": task_id,
        "step_id": step_id,
        "step_seq": step_seq,
        "action": action,
        "args": dict(args),
        "level_index": level_index,
        "capabilities": sorted(set(capabilities)),
    }
    payload = _canonical_dumps(record)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return record, digest


def compute_plan_intent_sha256(
    *,
    task_id: str,
    execution_mode: str,
    step_records: list[dict[str, Any]],
) -> str:
    """Hash of ordered step intent records (for run-level audit)."""
    body = {
        "task_id": task_id,
        "execution_mode": execution_mode,
        "steps": step_records,
    }
    return hashlib.sha256(_canonical_dumps(body).encode("utf-8")).hexdigest()
