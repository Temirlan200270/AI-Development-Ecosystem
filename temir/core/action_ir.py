"""
Action IR v2: LLM output adapter — любой распространённый JSON Mercury/Gemini → список шагов {action, args}.

Native:
- {"action": str, "args": dict}
- {"actions": [{...}, ...]} — элементы могут быть как native, так и loose (command/cmd/tool)

Loose (Mercury / agentic JSON):
- {"command": str} → execute_shell
- {"cmd": str | list} → execute_shell (list → безопасная склейка для OS)
- {"tool": str, "args"|"arguments"|"parameters": ...}
- {"shell": str} → execute_shell
- {"text": str} → execute_shell только при allow_text_shell=True или TEMIR_IR_ALLOW_TEXT_SHELL=1 (по умолчанию IR v3: отклоняется)

Replay: хранить сырой envelope; исполнение всегда после normalize_tool_action_envelope().
"""
from __future__ import annotations

import json
import logging
import platform
import shlex
from subprocess import list2cmdline
from typing import Any

logger = logging.getLogger(__name__)


class ActionIRNormalizeError(ValueError):
    """LLM output cannot be mapped to executable tool steps."""


# Имена действий, которые LLM часто путают с нашим execute_shell(command=...).
_ACTION_NAME_ALIASES: dict[str, str] = {
    "shell": "execute_shell",
    "run_shell": "execute_shell",
    "terminal": "execute_shell",
    "exec": "execute_shell",
    "bash": "execute_shell",
    "sh": "execute_shell",
}

_TOOL_ARG_KEYS: tuple[str, ...] = ("args", "arguments", "parameters", "input")


def _cmd_payload_to_command_string(payload: Any) -> str:
    if isinstance(payload, str):
        s = payload.strip()
        if not s:
            raise ActionIRNormalizeError("'cmd' / 'command' must be a non-empty string")
        return s
    if isinstance(payload, list):
        if len(payload) == 0:
            raise ActionIRNormalizeError("'cmd' list must be non-empty")
        parts = [str(x) for x in payload]
        if platform.system() == "Windows":
            return list2cmdline(parts)
        return shlex.join(parts)
    raise ActionIRNormalizeError(
        f"'cmd' must be str or list, got {type(payload).__name__}",
    )


def _coerce_dict_args(val: Any) -> dict[str, Any]:
    if val is None:
        return {}
    if isinstance(val, dict):
        return dict(val)
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            logger.debug("tool args string is not JSON; using empty dict")
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _normalize_execute_shell_args(args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    if "command" not in out and "cmd" in out:
        out["command"] = _cmd_payload_to_command_string(out.pop("cmd"))
    return out


def _apply_action_aliases(action: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    act = _ACTION_NAME_ALIASES.get(action, action)
    a = dict(args)
    if act == "execute_shell":
        a = _normalize_execute_shell_args(a)
    return act, a


def _adapt_mercury_loose_dict(
    raw: dict[str, Any],
    *,
    allow_text_shell: bool,
) -> list[dict[str, Any]] | None:
    """
    Map a dict without top-level 'action' / 'actions' to IR steps, or None if no rule matches.
    """
    keys = set(raw.keys())

    if "tool" in raw and raw["tool"]:
        tool_name = str(raw["tool"]).strip()
        if not tool_name:
            return None
        merged: dict[str, Any] = {}
        for tk in _TOOL_ARG_KEYS:
            if tk in raw:
                merged.update(_coerce_dict_args(raw[tk]))
        act, args = _apply_action_aliases(tool_name, merged)
        return [{"action": act, "args": args}]

    if "command" in raw and isinstance(raw["command"], str):
        cmd = _cmd_payload_to_command_string(raw["command"])
        return [{"action": "execute_shell", "args": {"command": cmd}}]

    if "cmd" in raw:
        cmd = _cmd_payload_to_command_string(raw["cmd"])
        return [{"action": "execute_shell", "args": {"command": cmd}}]

    if "shell" in raw and isinstance(raw["shell"], str):
        cmd = _cmd_payload_to_command_string(raw["shell"])
        return [{"action": "execute_shell", "args": {"command": cmd}}]

    if "text" in raw and isinstance(raw["text"], str):
        if keys <= frozenset({"text", "timeout"}):
            if not allow_text_shell:
                raise ActionIRNormalizeError(
                    "Loose {'text': ...} shell mapping is disabled (IR v3). "
                    "Use {'action':'execute_shell','args':{'command':...}} or set "
                    "TEMIR_IR_ALLOW_TEXT_SHELL=1 for legacy recovery.",
                )
            cmd = _cmd_payload_to_command_string(raw["text"])
            extra: dict[str, Any] = {"command": cmd}
            if "timeout" in raw and raw["timeout"] is not None:
                try:
                    extra["timeout"] = int(raw["timeout"])
                except (TypeError, ValueError):
                    pass
            return [{"action": "execute_shell", "args": extra}]

    return None


def _normalize_one_step_dict(
    item: dict[str, Any],
    index_label: str,
    *,
    allow_text_shell: bool,
) -> list[dict[str, Any]]:
    """One plan element: native action or loose Mercury shape."""
    if item.get("action"):
        args = item.get("args", {})
        if not isinstance(args, dict):
            args = {}
        act, a = _apply_action_aliases(str(item["action"]), args)
        row: dict[str, Any] = {"action": act, "args": a}
        if isinstance(item.get("depends_on"), list):
            row["depends_on"] = [str(x) for x in item["depends_on"]]
        return [row]

    loose = _adapt_mercury_loose_dict(item, allow_text_shell=allow_text_shell)
    if loose:
        if isinstance(item.get("depends_on"), list):
            loose[0] = {
                **loose[0],
                "depends_on": [str(x) for x in item["depends_on"]],
            }
        return loose

    raise ActionIRNormalizeError(
        f"{index_label} has no recognizable tool contract (keys {sorted(item.keys())!r})",
    )


def normalize_tool_action_envelope(
    raw: dict[str, Any],
    *,
    allow_text_shell: bool | None = None,
) -> list[dict[str, Any]]:
    """
    Return a non-empty list of dicts, each with string 'action' and dict 'args'.

    allow_text_shell: if None, read env TEMIR_IR_ALLOW_TEXT_SHELL (default strict off).
    """
    import os

    if allow_text_shell is None:
        allow_text_shell = (os.environ.get("TEMIR_IR_ALLOW_TEXT_SHELL") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    if not isinstance(raw, dict):
        raise ActionIRNormalizeError(
            f"action_json must be a dict, got {type(raw).__name__}",
        )

    if "actions" in raw:
        actions = raw["actions"]
        if not isinstance(actions, list) or len(actions) == 0:
            raise ActionIRNormalizeError(
                "'actions' must be a non-empty list",
            )
        out: list[dict[str, Any]] = []
        for i, item in enumerate(actions):
            if not isinstance(item, dict):
                raise ActionIRNormalizeError(f"actions[{i}] must be a dict, got {type(item).__name__}")
            out.extend(
                _normalize_one_step_dict(
                    item,
                    f"actions[{i}]",
                    allow_text_shell=allow_text_shell,
                ),
            )
        return out

    top_action = raw.get("action")
    if top_action:
        args = raw.get("args", {})
        if not isinstance(args, dict):
            args = {}
        act, a = _apply_action_aliases(str(top_action), args)
        row = {"action": act, "args": a}
        if isinstance(raw.get("depends_on"), list):
            row["depends_on"] = [str(x) for x in raw["depends_on"]]
        return [row]

    loose = _adapt_mercury_loose_dict(raw, allow_text_shell=allow_text_shell)
    if loose:
        if isinstance(raw.get("depends_on"), list):
            loose[0] = {
                **loose[0],
                "depends_on": [str(x) for x in raw["depends_on"]],
            }
        return loose

    raise ActionIRNormalizeError(
        f"Need tool contract (action, actions, tool, command, cmd, …), got keys: {sorted(raw.keys())!r}",
    )
