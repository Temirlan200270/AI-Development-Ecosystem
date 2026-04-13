"""IR v4: parallel writes then git_add depending on both (explicit DAG)."""
import pytest

from temir.core.ir_v3 import compile_llm_json_to_execution_plan_v3
from temir.core.tool_registry import ToolRegistry


class _Tools:
    def write_file(self, content: str, path: str, overwrite: bool = True) -> bool:
        return True

    def git_add(self, files: list) -> dict:
        return {"success": True}

    def cleanup(self) -> None:
        pass


def test_explicit_dag_git_after_parallel_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMIR_IR_DAG_IMPLICIT_LINEAR", "0")
    reg = ToolRegistry.from_tools(_Tools())
    plan = compile_llm_json_to_execution_plan_v3(
        {
            "execution_mode": "dag",
            "actions": [
                {"action": "write_file", "args": {"path": "a.txt", "content": "1"}},
                {"action": "write_file", "args": {"path": "b.txt", "content": "2"}},
                {
                    "action": "git_add",
                    "args": {"files": ["a.txt", "b.txt"]},
                    "depends_on": ["t:step:0", "t:step:1"],
                },
            ],
        },
        task_id="t",
        registry=reg,
    )
    assert plan.ir_generation == "v4"
    assert plan.steps[2].depends_on == ["t:step:0", "t:step:1"]
    from temir.core.execution_graph import topological_levels

    levels = topological_levels(plan.steps)
    assert len(levels) == 2
    assert len(levels[0]) == 2
    assert len(levels[1]) == 1
    assert levels[1][0].action == "git_add"
