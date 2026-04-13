"""Публикация событий пайплайна в DebugEventHub (оркестратор не зависит от UI)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, overload

from temir.web.event_envelope import EventEnvelope

logger = logging.getLogger(__name__)


@overload
async def publish_pipeline_event(topic: str, payload: Optional[Dict[str, Any]] = None) -> None: ...


@overload
async def publish_pipeline_event(envelope: EventEnvelope) -> None: ...


async def publish_pipeline_event(
    topic_or_envelope: str | EventEnvelope,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Отправляет событие подписчикам WebSocket.
    Ошибки глушатся: GUI опционален, ядро не должно падать.
    Поддержка EventEnvelope — единый контракт трассировки и времени события.
    """
    try:
        from temir.web.hub import get_debug_hub

        hub = get_debug_hub()
        if isinstance(topic_or_envelope, EventEnvelope):
            await hub.publish_envelope(topic_or_envelope)
        else:
            await hub.publish(topic_or_envelope, payload)
    except Exception as e:
        label = (
            topic_or_envelope.event_type
            if isinstance(topic_or_envelope, EventEnvelope)
            else topic_or_envelope
        )
        logger.debug("pipeline event %s skipped: %s", label, e)


def summarize_tool_action(action_name: str, action_args: Dict[str, Any]) -> Dict[str, Any]:
    """Укороченный payload для UI (без гигантских строк)."""
    out: Dict[str, Any] = {"action": action_name, "arg_keys": list(action_args.keys())}
    if action_name == "write_file":
        out["path"] = action_args.get("path")
        content = action_args.get("content")
        if isinstance(content, str):
            out["content_len"] = len(content)
            out["content_preview"] = content[:800] + ("…" if len(content) > 800 else "")
    elif action_name == "append_file":
        out["path"] = action_args.get("path")
        content = action_args.get("content")
        if isinstance(content, str):
            out["content_len"] = len(content)
            out["content_preview"] = content[:400] + ("…" if len(content) > 400 else "")
    elif action_name == "execute_shell":
        cmd = action_args.get("command", "")
        if isinstance(cmd, str):
            out["command_preview"] = cmd[:500] + ("…" if len(cmd) > 500 else "")
    else:
        for key in ("path", "file_path", "dir_path"):
            if key in action_args:
                out[key] = action_args.get(key)
    return out
