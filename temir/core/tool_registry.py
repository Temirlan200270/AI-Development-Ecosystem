"""
Registry of executable tool callables — source of truth for allowed action names + dispatch.

Built from AgentTools (public callables minus denylist). Orchestrator dispatches via .get(name),
not string getattr on arbitrary attributes.
"""
from __future__ import annotations

from typing import Any, Callable, FrozenSet, Mapping, Optional

from temir.core.action_preflight import collect_tool_allowlist


class ToolRegistry:
    """Maps tool name → bound method / function."""

    __slots__ = ("_callables",)

    def __init__(self, callables: Mapping[str, Callable[..., Any]]) -> None:
        self._callables = dict(callables)

    @classmethod
    def from_tools(cls, tools: Any) -> ToolRegistry:
        names = collect_tool_allowlist(tools)
        return cls({n: getattr(tools, n) for n in names})

    def get(self, name: str) -> Optional[Callable[..., Any]]:
        return self._callables.get(name)

    @property
    def names(self) -> FrozenSet[str]:
        return frozenset(self._callables.keys())

    def allowed_actions_hint(self) -> str:
        return ", ".join(sorted(self._callables.keys()))
