"""
Execution-aware preflight: validate normalized tool steps before any sandbox I/O.

- unknown_action → not on AgentTools public callables allowlist (repair_hint lists tools)
- blocked_path → escapes project root or matches sensitive path heuristics
- blocked_command → execute_shell matches destructive / system patterns

Replay: journal still stores raw LLM envelope; preflight runs at execute time only.

Environment (orchestrator):
- TEMIR_PREFLIGHT_REPAIR_ATTEMPTS: extra LLM rounds after IR v2 normalize, IR v3 schema, or preflight
  violation (default 0). Retry appends CONTRACT_REPAIR to the task prompt and bypasses task cache.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, FrozenSet, Iterable, Mapping, Sequence

from temir.core.platform_context import (
    PlatformContext,
    execute_shell_platform_mismatch_reason,
    platform_repair_hint,
    resolve_platform_context,
)

# IR v3: shell length / obfuscation heuristics
_MAX_SHELL_COMMAND_LEN = 12000

# Callable names on AgentTools we never expose to the LLM tool protocol.
_TOOL_NAME_DENYLIST: FrozenSet[str] = frozenset({"cleanup"})

# Case-insensitive substrings: reject paths (before/after resolve) and shell one-liners.
_BLOCKED_PATH_MARKERS: tuple[str, ...] = (
    "system32",
    "syswow64",
    "\\windows\\system32",
    "/windows/system32",
    "/etc/passwd",
    "/etc/shadow",
    "/proc/",
    "/dev/",
    ":\\program files\\",
    "program files (x86)",
    ":\\programdata\\",
)

# Shell command blocklist (substring, case-insensitive).
_BLOCKED_SHELL_MARKERS: tuple[str, ...] = (
    "system32",
    "syswow64",
    ":\\windows",
    "/etc/",
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    ":(){",
    "shutdown",
    "reboot",
    "format ",
    "diskpart",
    "reg delete",
    "bcdedit",
    "cipher /w",
    "> nul &",  # obfuscation hint
    "invoke-expression",
    "iex(",
    "downloadstring",
    "bitsadmin",
    "certutil -urlcache",
    "del /f /s /q",
    ":(){ :|:& };:",
)

# Map tool name → argument keys that carry filesystem paths (values may be str or list[str]).
_PATH_ARG_KEYS_BY_TOOL: Mapping[str, tuple[str, ...]] = {
    "write_file": ("path",),
    "read_file": ("path",),
    "append_file": ("path",),
    "smart_patch": ("path",),
    "create_directory": ("dir_path", "path"),
    "list_directory": ("dir_path", "path"),
    "remove_path": ("path",),
    "copy_path": ("source", "destination"),
    "run_tests": ("path",),
    "run_linter": ("path",),
    "file_exists": ("filename",),
    "directory_exists": ("dir_path",),
    "git_add": ("files",),
}


class ActionPreflightViolation(Exception):
    """A tool step failed static execution policy checks."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        repair_hint: str | None = None,
    ) -> None:
        self.code = code
        self.repair_hint = repair_hint
        full = message
        if repair_hint:
            full = f"{message} | repair_hint: {repair_hint}"
        super().__init__(full)


def collect_tool_allowlist(tools: Any) -> FrozenSet[str]:
    """Public callables on the toolbox, minus denylist (e.g. cleanup)."""
    names: list[str] = []
    for name in dir(tools):
        if name.startswith("_"):
            continue
        if name in _TOOL_NAME_DENYLIST:
            continue
        attr = getattr(tools, name, None)
        if callable(attr):
            names.append(name)
    return frozenset(names)


def _lower_s(s: str) -> str:
    return s.lower().replace("\\", "/")


def _path_string_toxic(raw: str) -> bool:
    s = _lower_s(raw)
    return any(m in s for m in _BLOCKED_PATH_MARKERS)


def _shell_toxic(command: str) -> bool:
    s = command.lower()
    return any(m in s for m in _BLOCKED_SHELL_MARKERS)


def _shell_entropy_violation(command: str) -> str | None:
    if len(command) > _MAX_SHELL_COMMAND_LEN:
        return f"command exceeds max length ({_MAX_SHELL_COMMAND_LEN})"
    low = command.lower()
    if re.search(r"(?i)(base64\s*--decode|base64\s+-d|\\|frombase64|certutil.*decode)", low):
        return "suspected encoded payload / decoder chain"
    # long token without spaces (possible blob)
    for token in re.findall(r"\S+", command):
        if len(token) > 4096 and re.match(r"^[A-Za-z0-9+/=_-]+$", token):
            return "suspected long encoded token in command"
    return None


def _iter_path_values(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str) and value.strip():
        yield value.strip()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, str) and item.strip():
                yield item.strip()


def _ensure_under_project(path_arg: str, project_root: Path) -> None:
    raw = path_arg.strip()
    if not raw:
        return
    if _path_string_toxic(raw):
        raise ActionPreflightViolation(
            "blocked_path",
            f"Path argument blocked by policy: {path_arg!r}",
            repair_hint="Use paths only inside output_dir; avoid system directories.",
        )
    candidate = Path(raw)
    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    except OSError as e:
        raise ActionPreflightViolation(
            "blocked_path",
            f"Invalid path {path_arg!r}: {e}",
        ) from e

    root = project_root.resolve()
    if resolved == root:
        return
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ActionPreflightViolation(
            "blocked_path",
            f"Path escapes output directory: {path_arg!r} → {resolved}",
            repair_hint="Use relative paths under the project root (e.g. cli_tool/main.py).",
        ) from None

    try:
        if resolved.exists() and resolved.is_symlink():
            raise ActionPreflightViolation(
                "blocked_path",
                f"Symlink paths are not allowed: {path_arg!r}",
                repair_hint="Use real files/directories under output_dir, not symlinks.",
            )
    except OSError:
        pass


def _validate_step_paths(
    action: str,
    args: Mapping[str, Any],
    project_root: Path,
) -> None:
    keys = _PATH_ARG_KEYS_BY_TOOL.get(action)
    if not keys:
        return
    for key in keys:
        if key not in args:
            continue
        for p in _iter_path_values(args[key]):
            _ensure_under_project(p, project_root)


def _validate_execute_shell(args: Mapping[str, Any], platform: PlatformContext) -> None:
    cmd = args.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ActionPreflightViolation(
            "invalid_args",
            "execute_shell requires non-empty string 'command'.",
            repair_hint='Use {"action":"execute_shell","args":{"command":"..."}} with a bounded dev command.',
        )
    # Shell vs OS: same policy as compile_llm_json_to_execution_plan_v3 (SSOT).
    mismatch = execute_shell_platform_mismatch_reason(cmd, platform)
    if mismatch:
        raise ActionPreflightViolation(
            "platform_mismatch",
            f"execute_shell does not match runtime ({platform.os}): {mismatch}",
            repair_hint=platform_repair_hint(platform),
        )
    if _shell_toxic(cmd):
        raise ActionPreflightViolation(
            "blocked_command",
            f"Shell command blocked by policy: {cmd[:200]!r}",
            repair_hint="Use only project-scoped commands (pytest, ruff, git, python -m ...) without system paths.",
        )
    # Windows drive-root or Unix absolute outside cwd often indicates overreach
    if re.search(r"(?i)(^[a-z]:\\(windows|program files|programdata)\\|^[a-z]:\\\\)", cmd.strip()):
        raise ActionPreflightViolation(
            "blocked_command",
            "Shell command references system root paths.",
            repair_hint="Run commands relative to the sandbox working directory only.",
        )
    entropy = _shell_entropy_violation(cmd)
    if entropy:
        raise ActionPreflightViolation(
            "blocked_command",
            f"Shell policy (entropy): {entropy}",
            repair_hint="Use short, explicit dev commands without encoded blobs.",
        )


def preflight_tool_steps(
    steps: Sequence[Mapping[str, Any]],
    *,
    project_root: Path,
    registry: Any,
    platform: PlatformContext | None = None,
) -> None:
    """
    Raise ActionPreflightViolation if any step is not executable under policy.

    steps: normalized + schema-validated dicts from ToolAction.model_dump().
    registry: ToolRegistry (allowed names = registry.names).
    platform: если None — resolve_platform_context(None) (env + детект ОС).
    """
    allow = registry.names
    root = project_root.resolve()
    plat = platform if platform is not None else resolve_platform_context(None)

    for i, step in enumerate(steps):
        action = step.get("action")
        if not isinstance(action, str) or not action:
            raise ActionPreflightViolation(
                "invalid_step",
                f"Step {i} has no action string: {step!r}",
            )
        args = step.get("args", {})
        if not isinstance(args, dict):
            args = {}

        if action not in allow:
            preview = ", ".join(sorted(allow))
            raise ActionPreflightViolation(
                "unknown_action",
                f"Step {i}: unknown or disallowed action {action!r}.",
                repair_hint=f"Use 'action' as one of: {preview}",
            )

        if action == "execute_shell":
            _validate_execute_shell(args, plat)
        else:
            _validate_step_paths(action, args, root)


def preflight_repair_context_message(violation: ActionPreflightViolation) -> str:
    """One line to append when asking the LLM to emit a revised tool JSON."""
    hint = violation.repair_hint or (violation.args[0] if violation.args else str(violation))
    return f"Contract repair ({violation.code}): {hint}"
