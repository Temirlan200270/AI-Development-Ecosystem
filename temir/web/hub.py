"""Шина событий для GUI: подключение оркестратора через get_debug_hub().publish(...)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

from starlette.websockets import WebSocket

from temir.web.event_envelope import EventEnvelope


class DebugEventHub:
    """Рассылает JSON-сообщения всем подключённым WebSocket-клиентам."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.add(websocket)

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def publish(
        self,
        topic: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        session_id: str = "default",
    ) -> None:
        """Публикует событие (envelope Event Schema v1 + совместимое поле ts)."""
        from temir.web.event_schema import build_event_message

        message = await build_event_message(
            topic,
            payload,
            session_id=session_id,
        )
        await self.broadcast_raw(message)
        try:
            from temir.storage.event_journal import append_envelope_async

            await append_envelope_async(message)
        except Exception as e:
            logger.warning("event journal append failed: %s", e)

    async def publish_envelope(self, env: EventEnvelope) -> None:
        """Публикация доменного события (тот же wire + journal, что и publish)."""
        from temir.web.event_schema import build_event_message_from_envelope

        message = await build_event_message_from_envelope(env)
        await self.broadcast_raw(message)
        try:
            from temir.storage.event_journal import append_envelope_async

            await append_envelope_async(message)
        except Exception as e:
            logger.warning("event journal append failed: %s", e)

    async def broadcast_raw(self, message: Dict[str, Any]) -> None:
        text = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(text)
            except Exception as e:
                logger.debug("WebSocket send failed: %s", e)
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


_hub: Optional[DebugEventHub] = None


def get_debug_hub() -> DebugEventHub:
    """Синглтон хаба (один процесс = один UI-сервер)."""
    global _hub
    if _hub is None:
        _hub = DebugEventHub()
    return _hub
