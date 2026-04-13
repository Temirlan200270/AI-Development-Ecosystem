"""Execution-aware preflight for tool steps."""
from pathlib import Path

import pytest

from temir.core.action_preflight import (
    ActionPreflightViolation,
    collect_tool_allowlist,
    preflight_repair_context_message,
    preflight_tool_steps,
)
from temir.core.platform_context import PlatformContext
from temir.core.tool_registry import ToolRegistry


class _MiniTools:
    def write_file(self, content: str, path: str, overwrite: bool = True) -> bool:
        return True

    def execute_shell(self, command: str, timeout: int = 60) -> dict:
        return {"success": True}

    def cleanup(self) -> None:
        pass


def test_allowlist_excludes_cleanup_and_private() -> None:
    t = _MiniTools()
    allow = collect_tool_allowlist(t)
    assert "write_file" in allow
    assert "execute_shell" in allow
    assert "cleanup" not in allow


def _reg() -> ToolRegistry:
    return ToolRegistry.from_tools(_MiniTools())


def test_preflight_ok_relative_paths(tmp_path: Path) -> None:
    steps = [
        {"action": "write_file", "args": {"path": "pkg/main.py", "content": "x"}},
    ]
    preflight_tool_steps(steps, project_root=tmp_path, registry=_reg())


def test_unknown_action(tmp_path: Path) -> None:
    steps = [{"action": "delete_system32", "args": {}}]
    with pytest.raises(ActionPreflightViolation) as exc:
        preflight_tool_steps(steps, project_root=tmp_path, registry=_reg())
    assert exc.value.code == "unknown_action"
    assert exc.value.repair_hint
    assert "write_file" in exc.value.repair_hint


def test_blocked_path_marker(tmp_path: Path) -> None:
    steps = [{"action": "write_file", "args": {"path": "x/system32/evil", "content": ""}}]
    with pytest.raises(ActionPreflightViolation) as exc:
        preflight_tool_steps(steps, project_root=tmp_path, registry=_reg())
    assert exc.value.code == "blocked_path"


def test_path_escapes_project(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    outside = root.parent / "outside_file"
    steps = [{"action": "write_file", "args": {"path": str(outside), "content": ""}}]
    with pytest.raises(ActionPreflightViolation) as exc:
        preflight_tool_steps(steps, project_root=root, registry=_reg())
    assert exc.value.code == "blocked_path"


def test_execute_shell_allowed_dev_command(tmp_path: Path) -> None:
    steps = [{"action": "execute_shell", "args": {"command": "pytest -q tests"}}]
    preflight_tool_steps(steps, project_root=tmp_path, registry=_reg())


def test_execute_shell_platform_mismatch_windows(tmp_path: Path) -> None:
    steps = [
        {
            "action": "execute_shell",
            "args": {"command": '/bin/bash -lc "ls -R"'},
        },
    ]
    win = PlatformContext(os="windows", shell="powershell")
    with pytest.raises(ActionPreflightViolation) as exc:
        preflight_tool_steps(
            steps,
            project_root=tmp_path,
            registry=_reg(),
            platform=win,
        )
    assert exc.value.code == "platform_mismatch"
    assert exc.value.repair_hint
    assert "platform=windows" in exc.value.repair_hint


def test_execute_shell_blocked(tmp_path: Path) -> None:
    steps = [
        {
            "action": "execute_shell",
            "args": {"command": "del /f C:\\Windows\\System32\\*"},
        },
    ]
    with pytest.raises(ActionPreflightViolation) as exc:
        preflight_tool_steps(steps, project_root=tmp_path, registry=_reg())
    assert exc.value.code == "blocked_command"


def test_repair_context_includes_code() -> None:
    v = ActionPreflightViolation(
        "unknown_action",
        "bad",
        repair_hint="use write_file",
    )
    msg = preflight_repair_context_message(v)
    assert "unknown_action" in msg
    assert "write_file" in msg