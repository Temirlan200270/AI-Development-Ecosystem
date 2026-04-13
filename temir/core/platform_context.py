"""
Единый контракт runtime ОС для инструментов (без автоперевода команд).

execute_shell не портируем: на Windows отклоняем явные Unix-инвокации (fail fast + repair loop).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Literal, Mapping

PlatformOs = Literal["windows", "linux", "mac"]
PlatformShell = Literal["powershell", "bash", "sh", "cmd"]


@dataclass(frozen=True)
class PlatformContext:
    os: PlatformOs
    shell: PlatformShell


def detect_platform_os() -> PlatformOs:
    pl = sys.platform
    if pl == "win32":
        return "windows"
    if pl == "darwin":
        return "mac"
    return "linux"


def default_shell_for_os(os_name: PlatformOs) -> PlatformShell:
    if os_name == "windows":
        return "powershell"
    return "bash"


def resolve_platform_context(config: Mapping[str, Any] | None = None) -> PlatformContext:
    """
    Порядок: env TEMIR_PLATFORM_OS / TEMIR_PLATFORM_SHELL,
    затем config['platform'] = {os, shell} или platform_os / platform_shell,
    иначе авто-детект по sys.platform.
    """
    raw_os = (os.environ.get("TEMIR_PLATFORM_OS") or "").strip().lower()
    raw_shell = (os.environ.get("TEMIR_PLATFORM_SHELL") or "").strip().lower()

    cfg = dict(config) if config is not None else {}
    nested = cfg.get("platform")
    if isinstance(nested, dict):
        raw_os = raw_os or str(nested.get("os") or "").strip().lower()
        raw_shell = raw_shell or str(nested.get("shell") or "").strip().lower()
    else:
        raw_os = raw_os or str(cfg.get("platform_os") or "").strip().lower()
        raw_shell = raw_shell or str(cfg.get("platform_shell") or "").strip().lower()

    if raw_os in ("windows", "linux", "mac"):
        os_name: PlatformOs = raw_os  # type: ignore[assignment]
    else:
        os_name = detect_platform_os()

    if raw_shell in ("powershell", "bash", "sh", "cmd"):
        shell: PlatformShell = raw_shell  # type: ignore[assignment]
    else:
        shell = default_shell_for_os(os_name)

    return PlatformContext(os=os_name, shell=shell)


def platform_event_fields(ctx: PlatformContext) -> dict[str, str]:
    """Плоские поля для payload событий журнала (replay / отладка)."""
    return {"platform_os": ctx.os, "platform_shell": ctx.shell}


def platform_repair_hint(ctx: PlatformContext) -> str:
    return (
        f"platform={ctx.os}, shell={ctx.shell}; "
        "regenerate execute_shell using only commands valid on this OS "
        "(Windows: cmd/PowerShell, e.g. dir, do not use /bin/bash, bash -c, bash -lc, Unix rm -rf)."
    )


def execute_shell_platform_mismatch_reason(command: str, ctx: PlatformContext) -> str | None:
    """
    Единая политика «shell ↔ platform» для IR compile и preflight.

    Оба слоя должны вызывать только эту функцию, чтобы причина отказа не расходилась.
    (Compile = контракт плана; preflight = повторная страховка перед I/O.)
    """
    if ctx.os != "windows":
        return None
    s = command.strip()
    if not s:
        return None
    low = s.lower()
    norm = low.replace("\\", "/")

    for frag in (
        "/bin/bash",
        "/bin/sh",
        "/usr/bin/",
        "bash -lc",
        "bash -c",
    ):
        if frag in norm:
            return (
                f"command contains {frag!r}, which is not a Windows-native shell contract"
            )

    if re.search(r"(?i)(^|[\s;&|])bash\s+-\s", s):
        return (
            "bash with dash-args is not supported on native Windows; use cmd/PowerShell"
        )

    if re.search(
        r"(?i)(^|[\s;&|])rm\s+(-[a-z]*rf[a-z]*|-[a-z]*fr[a-z]*)\b",
        s,
    ):
        return "Unix-style rm -rf is not portable; use Windows del / rmdir / Remove-Item"

    return None
