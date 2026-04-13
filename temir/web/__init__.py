"""Debug Control Panel: лёгкий Web UI к оркестратору (REST + WebSocket)."""

from temir.web.hub import DebugEventHub, get_debug_hub

__all__ = ["DebugEventHub", "get_debug_hub"]
