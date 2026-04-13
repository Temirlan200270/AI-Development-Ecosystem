"""Append-only JSONL journal и загрузка ранов."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from temir.storage.event_journal import append_envelope_async
from temir.storage.run_store import list_run_ids, load_run_events


@pytest.fixture
def journal_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TEMIR_EVENT_JOURNAL_DIR", str(tmp_path))
    return tmp_path


def _sample_envelope(run_id: str, seq: int, topic: str) -> dict:
    return {
        "event_id": f"e-{seq}",
        "run_id": run_id,
        "pipeline_id": run_id,
        "seq": seq,
        "timestamp": float(seq),
        "schema_version": "1.0",
        "topic": topic,
        "payload": {"task_id": "t1", "executor": "planner"},
        "session_id": "default",
        "ts": "2020-01-01T00:00:00+00:00",
    }


def test_list_runs_empty(journal_home: Path) -> None:
    assert list_run_ids() == []


def test_append_and_load(journal_home: Path) -> None:
    async def _go() -> None:
        await append_envelope_async(_sample_envelope("run-a", 1, "task.started"))
        await append_envelope_async(_sample_envelope("run-a", 2, "task.completed"))

    asyncio.run(_go())
    assert list_run_ids() == ["run-a"]
    ev = load_run_events("run-a")
    assert len(ev) == 2
    assert ev[0]["seq"] == 1
    assert ev[1]["topic"] == "task.completed"


def test_two_runs(journal_home: Path) -> None:
    async def _go() -> None:
        await append_envelope_async(_sample_envelope("z-run", 1, "pipeline.started"))
        await append_envelope_async(_sample_envelope("a-run", 1, "pipeline.started"))

    asyncio.run(_go())
    assert list_run_ids() == ["a-run", "z-run"]
