"""Цепочка моделей Gemini: порядок по умолчанию и env."""

from __future__ import annotations

import pytest

from temir.agents.gemini_chain import (
    DEFAULT_GEMINI_CHAIN,
    get_gemini_model_chain,
    should_try_next_gemini_model,
)


def test_default_chain_order() -> None:
    assert DEFAULT_GEMINI_CHAIN[0] == "gemini-3.1-pro-preview"
    assert "gemini-2.5-pro" in DEFAULT_GEMINI_CHAIN
    assert DEFAULT_GEMINI_CHAIN[-1] == "gemini-2.5-flash"


def test_chain_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL_CHAIN", "a,b,c")
    assert get_gemini_model_chain() == ["a", "b", "c"]


def test_should_fallback_on_quota_phrase() -> None:
    assert should_try_next_gemini_model(RuntimeError("Resource exhausted quota")) is True


def test_should_not_fallback_on_random() -> None:
    assert should_try_next_gemini_model(ValueError("invalid json")) is False
