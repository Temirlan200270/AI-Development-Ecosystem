"""
Execution graph semantics for IR v3: DAG validation, topological levels, parallel-safe policy.

Sequential mode: one step per level (legacy).
DAG mode: Kahn levels; within a level, parallel-safe tools may run under asyncio.gather.
"""
from __future__ import annotations

from typing import Any, FrozenSet, List, Sequence

# Действия без оболочки / git / тестов в отдельном процессе — допускаем gather в одном уровне.
PARALLEL_SAFE_ACTIONS: FrozenSet[str] = frozenset(
    {
        "write_file",
        "read_file",
        "append_file",
        "create_directory",
        "file_exists",
        "directory_exists",
        "list_directory",
        "remove_path",
        "copy_path",
        "smart_patch",
    },
)


class ExecutionGraphError(ValueError):
    """Invalid or cyclic step dependency graph."""


def _step_id(s: Any) -> str:
    return str(s.id)


def _step_deps(s: Any) -> List[str]:
    d = getattr(s, "depends_on", None)
    if not d:
        return []
    return [str(x) for x in d]


def _step_action(s: Any) -> str:
    return str(s.action)


def validate_acyclic(steps: Sequence[Any]) -> None:
    """Ensure all depends_on reference existing ids and graph has no cycles."""
    by_id = {_step_id(s): s for s in steps}
    if len(by_id) != len(steps):
        raise ExecutionGraphError("duplicate step id in plan")

    for s in steps:
        for dep in _step_deps(s):
            if dep not in by_id:
                raise ExecutionGraphError(
                    f"step {_step_id(s)!r} depends on unknown id {dep!r}",
                )

    in_degree: dict[str, int] = {_step_id(s): 0 for s in steps}
    children: dict[str, list[str]] = {_step_id(s): [] for s in steps}
    for s in steps:
        for dep in _step_deps(s):
            children[dep].append(_step_id(s))
            in_degree[_step_id(s)] += 1

    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    seen = 0
    while queue:
        u = queue.pop()
        seen += 1
        for v in children[u]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    if seen != len(steps):
        raise ExecutionGraphError("cyclic or unsatisfiable depends_on in step graph")


def topological_levels(steps: Sequence[Any]) -> List[List[Any]]:
    """Return ordered levels; steps within a level are mutually ready."""
    validate_acyclic(steps)
    by_id = {_step_id(s): s for s in steps}
    in_degree: dict[str, int] = {_step_id(s): 0 for s in steps}
    children: dict[str, list[str]] = {_step_id(s): [] for s in steps}
    for s in steps:
        for dep in _step_deps(s):
            children[dep].append(_step_id(s))
            in_degree[_step_id(s)] += 1

    levels: List[List[Any]] = []
    current_ids = [sid for sid, deg in in_degree.items() if deg == 0]
    processed = 0
    while current_ids:
        level = [by_id[sid] for sid in current_ids]
        levels.append(level)
        processed += len(current_ids)
        next_ids: list[str] = []
        for sid in current_ids:
            for v in children[sid]:
                in_degree[v] -= 1
                if in_degree[v] == 0:
                    next_ids.append(v)
        current_ids = next_ids

    if processed != len(steps):
        raise ExecutionGraphError("internal graph level error")
    return levels


def _plan_mode_value(plan: Any) -> str:
    m = getattr(plan, "execution_mode", "sequential")
    if hasattr(m, "value"):
        return str(m.value)
    return str(m)


def execution_levels_for_plan(plan: Any) -> List[List[Any]]:
    if _plan_mode_value(plan) == "sequential":
        return [[s] for s in plan.steps]
    return topological_levels(plan.steps)


def level_allows_parallel_gather(level: Sequence[Any]) -> bool:
    if len(level) <= 1:
        return False
    return all(_step_action(s) in PARALLEL_SAFE_ACTIONS for s in level)
