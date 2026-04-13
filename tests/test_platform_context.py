"""Runtime platform contract for execute_shell (Windows vs Unix)."""

from temir.core.platform_context import (
    PlatformContext,
    execute_shell_platform_mismatch_reason,
    platform_event_fields,
    resolve_platform_context,
)


def test_mismatch_bash_path_on_windows() -> None:
    ctx = PlatformContext(os="windows", shell="powershell")
    assert execute_shell_platform_mismatch_reason('/bin/bash -lc "ls"', ctx)
    assert execute_shell_platform_mismatch_reason("bash -c echo", ctx)


def test_ok_powershellish_on_windows() -> None:
    ctx = PlatformContext(os="windows", shell="powershell")
    assert execute_shell_platform_mismatch_reason("dir /s /b", ctx) is None
    assert execute_shell_platform_mismatch_reason("pytest -q", ctx) is None


def test_unix_allowed_on_linux() -> None:
    ctx = PlatformContext(os="linux", shell="bash")
    assert execute_shell_platform_mismatch_reason('/bin/bash -lc "ls -R"', ctx) is None


def test_platform_event_fields() -> None:
    d = platform_event_fields(PlatformContext(os="windows", shell="powershell"))
    assert d == {"platform_os": "windows", "platform_shell": "powershell"}


def test_resolve_from_mapping() -> None:
    p = resolve_platform_context({"platform": {"os": "windows", "shell": "cmd"}})
    assert p.os == "windows"
    assert p.shell == "cmd"
