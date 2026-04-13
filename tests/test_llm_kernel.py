"""LLM Kernel v1: transport retry heuristic и успешный путь (mock)."""

from __future__ import annotations

import pytest

from temir.llm.kernel import LLMKernel


def test_transient_transport_error_detects_timeout() -> None:
    from temir.llm import kernel as k

    assert k._transient_transport_error("Connection timeout") is True
    assert k._transient_transport_error("HTTP 503") is True


def test_transient_transport_error_rejects_business_logic() -> None:
    from temir.llm import kernel as k

    assert k._transient_transport_error("invalid API key") is False


@pytest.mark.asyncio
async def test_generate_gemini_success_without_events(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_gen(
        _prompt: str,
        *,
        models=None,
        rate_limiter=None,
    ):
        return {
            "success": True,
            "content": "  hello ",
            "usage": {"input_tokens": 3, "output_tokens": 4},
            "billing_model": "gemini-2.5-flash",
        }

    monkeypatch.setattr(
        "temir.llm.kernel.gemini_generate_content",
        fake_gen,
    )
    k = LLMKernel(emit_events=False)
    r = await k.generate_gemini("prompt", role_hint="TEST", task_id="t1")
    assert r.success is True
    assert r.text == "hello"
    assert r.billing_model == "gemini-2.5-flash"
    assert r.usage == {"input_tokens": 3, "output_tokens": 4}
    assert r.provider == "gemini"
    assert r.latency_ms >= 0
