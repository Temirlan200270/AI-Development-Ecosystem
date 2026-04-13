"""Контракт событий v1: envelope, реестр топиков, strict/soft валидация."""

from __future__ import annotations

import asyncio

import pytest

from temir.web.event_schema import (
    SCHEMA_VERSION,
    build_event_message,
    validate_payload,
)
from temir.web.run_telemetry import attach_pipeline_run, detach_pipeline_run


def test_validate_task_started_ok() -> None:
    validate_payload("task.started", {"task_id": "1", "executor": "x"})


def test_validate_missing_key_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEMIR_EVENT_SCHEMA_STRICT", raising=False)
    validate_payload("task.started", {"task_id": "1"})


def test_validate_missing_key_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMIR_EVENT_SCHEMA_STRICT", "1")
    with pytest.raises(ValueError, match="missing payload keys"):
        validate_payload("task.started", {"task_id": "1"})


def test_validate_unknown_topic_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMIR_EVENT_SCHEMA_STRICT", "1")
    with pytest.raises(ValueError, match="unknown topic"):
        validate_payload("not.registered.topic", {})


def test_build_envelope_seq_and_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEMIR_EVENT_SCHEMA_STRICT", raising=False)

    async def _run() -> None:
        attach_pipeline_run()
        try:
            m1 = await build_event_message(
                "task.started",
                {"task_id": "a", "executor": "b"},
            )
            m2 = await build_event_message(
                "task.started",
                {"task_id": "c", "executor": "d"},
            )
            assert m1["seq"] < m2["seq"]
            assert m1["run_id"] == m2["run_id"]
            assert m1["pipeline_id"] == m2["pipeline_id"]
            assert m1["schema_version"] == SCHEMA_VERSION
            assert "event_id" in m1
            assert m1["topic"] == "task.started"
            assert m1["payload"]["task_id"] == "a"
        finally:
            detach_pipeline_run()

    asyncio.run(_run())
