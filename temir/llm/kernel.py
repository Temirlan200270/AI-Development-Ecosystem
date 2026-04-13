"""
LLM Kernel v1: вызов модели, latency, transport-retry, события llm.*.
Семантика ролей и парсинг JSON остаются в агентах.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from temir.agents.gemini_chain import gemini_generate_content

logger = logging.getLogger(__name__)

_PROVIDER_GEMINI = "gemini"


def _transient_transport_error(message: str) -> bool:
    m = (message or "").lower()
    needles = (
        "timeout",
        "timed out",
        "connection",
        "network",
        "503",
        "502",
        "504",
        "unavailable",
        "temporarily",
        "reset",
        "broken pipe",
        "eof",
    )
    return any(n in m for n in needles)


def _preview_error(msg: Optional[str], limit: int = 240) -> str:
    if not msg:
        return ""
    s = msg.replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


@dataclass(frozen=True)
class LLMGenerateResult:
    success: bool
    text: str
    usage: Dict[str, int]
    billing_model: str
    provider: str
    latency_ms: float
    error: Optional[str] = None


class LLMKernel:
    """Транспортный слой для Gemini (цепочка моделей внутри gemini_generate_content)."""

    def __init__(self, *, emit_events: bool = True) -> None:
        self.emit_events = emit_events

    async def generate_gemini(
        self,
        prompt: str,
        *,
        rate_limiter: Optional[Any] = None,
        role_hint: str = "",
        task_id: str = "",
        max_transport_retries: int = 2,
    ) -> LLMGenerateResult:
        """
        Один логический запрос: цепочка моделей + опциональные повторы при сетевых сбоях.
        """
        if self.emit_events:
            try:
                from temir.web.pipeline_events import publish_pipeline_event

                await publish_pipeline_event(
                    "llm.requested",
                    {
                        "provider": _PROVIDER_GEMINI,
                        "prompt_length": len(prompt),
                        "role_hint": role_hint or "",
                        "task_id": task_id or "",
                    },
                )
            except Exception as e:
                logger.debug("llm.requested event skipped: %s", e)

        last_error: Optional[str] = None
        total_latency_ms = 0.0
        attempts = max(0, int(max_transport_retries)) + 1

        for attempt in range(attempts):
            t0 = time.perf_counter()
            raw = await gemini_generate_content(
                prompt,
                rate_limiter=rate_limiter,
            )
            elapsed = (time.perf_counter() - t0) * 1000.0
            total_latency_ms += elapsed

            if raw.get("success"):
                usage = raw.get("usage") or {"input_tokens": 0, "output_tokens": 0}
                billing = raw.get("billing_model") or "gemini-2.5-flash"
                text = (raw.get("content") or "").strip()
                result = LLMGenerateResult(
                    success=True,
                    text=text,
                    usage={
                        "input_tokens": int(usage.get("input_tokens", 0) or 0),
                        "output_tokens": int(usage.get("output_tokens", 0) or 0),
                    },
                    billing_model=billing,
                    provider=_PROVIDER_GEMINI,
                    latency_ms=round(total_latency_ms, 3),
                    error=None,
                )
                await self._emit_completed(result)
                return result

            last_error = str(raw.get("error") or "unknown error")
            if attempt + 1 < attempts and _transient_transport_error(last_error):
                logger.warning(
                    "LLM transport retry %s/%s after: %s",
                    attempt + 1,
                    attempts,
                    _preview_error(last_error, 120),
                )
                await asyncio.sleep(2**attempt)
                continue
            break

        result = LLMGenerateResult(
            success=False,
            text="",
            usage={"input_tokens": 0, "output_tokens": 0},
            billing_model="",
            provider=_PROVIDER_GEMINI,
            latency_ms=round(total_latency_ms, 3),
            error=last_error,
        )
        await self._emit_completed(result)
        return result

    async def _emit_completed(self, result: LLMGenerateResult) -> None:
        if not self.emit_events:
            return
        try:
            from temir.web.pipeline_events import publish_pipeline_event

            await publish_pipeline_event(
                "llm.completed",
                {
                    "success": result.success,
                    "provider": result.provider,
                    "latency_ms": result.latency_ms,
                    "billing_model": result.billing_model or "",
                    "input_tokens": result.usage.get("input_tokens", 0),
                    "output_tokens": result.usage.get("output_tokens", 0),
                    "error_preview": _preview_error(result.error) if result.error else "",
                },
            )
        except Exception as e:
            logger.debug("llm.completed event skipped: %s", e)


_kernel: Optional[LLMKernel] = None


def get_llm_kernel(*, emit_events: bool = True) -> LLMKernel:
    """Синглтон ядра на процесс."""
    global _kernel
    if _kernel is None:
        _kernel = LLMKernel(emit_events=emit_events)
    return _kernel
