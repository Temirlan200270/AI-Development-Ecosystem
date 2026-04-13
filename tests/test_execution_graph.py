"""DAG levels and parallel-safe policy."""
import pytest

from temir.core.execution_graph import (
    ExecutionGraphError,
    level_allows_parallel_gather,
    topological_levels,
    validate_acyclic,
)
from temir.core.ir_v3 import (
    IRV3ContractError,
    StepMetaV3,
    StepMetaSource,
    StepV3,
    compile_llm_json_to_execution_plan_v3,
)
from temir.core.tool_registry import ToolRegistry


class _T:
    def write_file(self, content: str, path: str, overwrite: bool = True) -> bool:
        return True

    def execute_shell(self, command: str, timeout: int = 60) -> dict:
        return {"success": True}

    def cleanup(self) -> None:
        pass


def _meta(i: int, n: int, unsafe: bool = False) -> StepMetaV3:
    return StepMetaV3(
        index=i,
        total=n,
        retryable=True,
        max_retries=0,
        timeout_ms=None,
        unsafe=unsafe,
        source=StepMetaSource.LLM,
    )


def test_topological_two_roots() -> None:
    s0 = StepV3(
        id="s0",
        action="write_file",
        args={},
        depends_on=[],
        meta=_meta(0, 2),
    )
    s1 = StepV3(
        id="s1",
        action="write_file",
        args={},
        depends_on=[],
        meta=_meta(1, 2),
    )
    levels = topological_levels([s0, s1])
    assert len(levels) == 1
    assert {levels[0][0].id, levels[0][1].id} == {"s0", "s1"}


def test_cycle_raises() -> None:
    s0 = StepV3(
        id="s0",
        action="write_file",
        args={},
        depends_on=["s1"],
        meta=_meta(0, 2),
    )
    s1 = StepV3(
        id="s1",
        action="write_file",
        args={},
        depends_on=["s0"],
        meta=_meta(1, 2),
    )
    with pytest.raises(ExecutionGraphError):
        validate_acyclic([s0, s1])


def test_level_parallel_two_writes() -> None:
    s0 = StepV3(id="a", action="write_file", args={}, depends_on=[], meta=_meta(0, 2))
    s1 = StepV3(id="b", action="write_file", args={}, depends_on=[], meta=_meta(1, 2))
    assert level_allows_parallel_gather([s0, s1]) is True


def test_level_not_parallel_with_shell() -> None:
    s0 = StepV3(
        id="a",
        action="write_file",
        args={},
        depends_on=[],
        meta=_meta(0, 2),
    )
    s1 = StepV3(
        id="b",
        action="execute_shell",
        args={"command": "echo"},
        depends_on=[],
        meta=_meta(1, 2, unsafe=True),
    )
    assert level_allows_parallel_gather([s0, s1]) is False


def test_compile_dag_cycle_irv3() -> None:
    reg = ToolRegistry.from_tools(_T())
    raw = {
        "execution_mode": "dag",
        "actions": [
            {
                "action": "write_file",
                "args": {"path": "a", "content": ""},
                "depends_on": ["t:step:1"],
            },
            {
                "action": "write_file",
                "args": {"path": "b", "content": ""},
                "depends_on": ["t:step:0"],
            },
        ],
    }
    with pytest.raises(IRV3ContractError) as exc:
        compile_llm_json_to_execution_plan_v3(raw, task_id="t", registry=reg)
    assert exc.value.code == "graph"


def test_compile_dag_fork_one_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMIR_IR_DAG_IMPLICIT_LINEAR", "0")
    reg = ToolRegistry.from_tools(_T())
    plan = compile_llm_json_to_execution_plan_v3(
        {
            "execution_mode": "dag",
            "actions": [
                {"action": "write_file", "args": {"path": "a", "content": "1"}},
                {"action": "write_file", "args": {"path": "b", "content": "2"}},
            ],
        },
        task_id="job",
        registry=reg,
    )
    levels = topological_levels(plan.steps)
    assert len(levels) == 1
    assert len(levels[0]) == 2
