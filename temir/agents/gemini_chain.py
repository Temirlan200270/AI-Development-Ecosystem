"""
Цепочка моделей Gemini: сначала мощная preview, при квоте/лимите — pro, затем flash.
Порядок по умолчанию: gemini-3.1-pro-preview -> gemini-2.5-pro -> gemini-2.5-flash
Переопределение: GEMINI_MODEL_CHAIN=model1,model2,model3
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_CHAIN: List[str] = [
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]


def get_gemini_model_chain() -> List[str]:
    raw = (os.environ.get("GEMINI_MODEL_CHAIN") or "").strip()
    if raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts:
            return parts
    return list(DEFAULT_GEMINI_CHAIN)


def should_try_next_gemini_model(exc: BaseException) -> bool:
    """Ошибки квоты / лимита / недоступности модели — переключаемся на следующую."""
    msg = str(exc).lower()
    try:
        import google.api_core.exceptions as gexc
    except ImportError:
        gexc = None

    if gexc is not None and isinstance(
        exc,
        (
            gexc.ResourceExhausted,
            gexc.TooManyRequests,
            gexc.ServiceUnavailable,
            gexc.DeadlineExceeded,
        ),
    ):
        return True

    patterns = (
        "quota",
        "exhausted",
        "rate limit",
        "resource has been exhausted",
        "429",
        "too many requests",
        "capacity",
        "try again later",
        "billing",
        "token count exceeds",
        "maximum context",
        "max tokens",
        "location is not supported",
    )
    if any(p in msg for p in patterns):
        return True
    if "not found" in msg and "model" in msg:
        return True
    if "404" in msg:
        return True
    return False


async def gemini_generate_content(
    prompt: str,
    *,
    models: Optional[List[str]] = None,
    rate_limiter: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    generate_content с перебором моделей. Нужен предварительный genai.configure(api_key=...).
    Успех: success, content, usage, billing_model (имя модели для CostCalculator).
    """
    try:
        import google.generativeai as genai
    except ImportError:
        return {"success": False, "error": "google-generativeai not installed"}

    chain = models if models is not None else get_gemini_model_chain()
    last_error: Optional[BaseException] = None

    for model_name in chain:
        try:
            if rate_limiter:
                await rate_limiter.acquire()
            model = genai.GenerativeModel(model_name)
            resp = await asyncio.to_thread(model.generate_content, prompt)
            text = ""
            if hasattr(resp, "text") and resp.text:
                text = resp.text.strip()
            if not text:
                last_error = RuntimeError("empty response")
                logger.warning(
                    "Gemini model %s returned empty text, trying next",
                    model_name,
                )
                continue
            usage = {"input_tokens": 0, "output_tokens": 0}
            if hasattr(resp, "usage_metadata") and resp.usage_metadata:
                usage["input_tokens"] = int(
                    getattr(resp.usage_metadata, "prompt_token_count", 0) or 0,
                )
                usage["output_tokens"] = int(
                    getattr(
                        resp.usage_metadata,
                        "candidates_token_count",
                        0,
                    )
                    or 0,
                )
            return {
                "success": True,
                "content": text,
                "usage": usage,
                "billing_model": model_name,
            }
        except Exception as e:
            last_error = e
            if should_try_next_gemini_model(e):
                logger.warning(
                    "Gemini model %s failed (%s), next in chain",
                    model_name,
                    e,
                )
                continue
            return {
                "success": False,
                "error": str(e),
                "billing_model": model_name,
            }

    return {
        "success": False,
        "error": str(last_error) if last_error else "all models in chain failed",
    }
