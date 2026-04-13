"""Общие фикстуры: журнал событий не пишет в реальный .andromeda во время pytest."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_event_journal(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path_factory.mktemp("event_journal")
    monkeypatch.setenv("TEMIR_EVENT_JOURNAL_DIR", str(root))
