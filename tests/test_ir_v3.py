"""IR v3 execution contract: compile + registry + meta."""
import pytest

from temir.core.ir_v3 import (
    IRV3ContractError,
    compile_llm_json_to_execution_plan_v3,
    plan_to_executor_dicts,
)
from temir.core.platform_context import PlatformContext
from temir.core.tool_registry import ToolRegistry


class _T:
    def write_file(self, content: str, path: str, overwrite: bool = True) -> bool:
        return True

    def execute_shell(self, command: str, timeout: int = 60) -> dict:
        return {"success": True}

    def remove_path(self, path: str) -> bool:
        return True

    def cleanup(self) -> None:
        pass


def test_compile_maps_delete_file_alias() -> None:
    reg = ToolRegistry.from_tools(_T())
    plan = compile_llm_json_to_execution_plan_v3(
        {"action": "delete_file", "args": {"path": "old.txt"}},
        task_id="t1",
        registry=reg,
    )
    assert plan.steps[0].action == "remove_path"
    assert plan.steps[0].meta.unsafe is False


def test_compile_rejects_unknown_action() -> None:
    reg = ToolRegistry.from_tools(_T())
    with pytest.raises(IRV3ContractError) as exc:
        compile_llm_json_to_execution_plan_v3(
            {"action": "rm_rf", "args": {}},
            task_id="t1",
            registry=reg,
        )
    assert exc.value.code == "unknown_action"


def test_batch_limit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMIR_IR_MAX_BATCH_STEPS", "2")
    reg = ToolRegistry.from_tools(_T())
    with pytest.raises(IRV3ContractError) as exc:
        compile_llm_json_to_execution_plan_v3(
            {
                "actions": [
                    {"action": "write_file", "args": {"path": "a", "content": ""}},
                    {"action": "write_file", "args": {"path": "b", "content": ""}},
                    {"action": "write_file", "args": {"path": "c", "content": ""}},
                ],
            },
            task_id="t1",
            registry=reg,
        )
    assert exc.value.code == "batch_limit"


def test_compile_rejects_unix_shell_on_windows() -> None:
    reg = ToolRegistry.from_tools(_T())
    win = PlatformContext(os="windows", shell="powershell")
    with pytest.raises(IRV3ContractError) as exc:
        compile_llm_json_to_execution_plan_v3(
            {"command": "bash -c ls"},
            task_id="t1",
            registry=reg,
            platform=win,
        )
    assert exc.value.code == "platform_mismatch"


def test_plan_to_executor_dicts() -> None:
    reg = ToolRegistry.from_tools(_T())
    plan = compile_llm_json_to_execution_plan_v3(
        {"command": "echo x"},
        task_id="t1",
        registry=reg,
    )
    d = plan_to_executor_dicts(plan)
    assert d[0]["action"] == "execute_shell"
    assert plan.steps[0].meta.unsafe is True
