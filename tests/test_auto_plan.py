import pytest
from pathlib import Path

from temir.core.models import AIRole
from temir.core.orchestrator import Orchestrator
from temir.sandbox.local_sandbox import LocalUnsafeSandbox
from temir.tools.agent_tools import AgentTools


class PlannerStub:
    def __init__(self):
        self.model = True  # чтобы не оффлайн

    async def execute_task(
        self,
        task_description,
        role,
        context=None,
        max_retries=3,
    ):
        if role == AIRole.SYSTEM_ARCHITECT:
            plan = {
                "execution_plan": [
                    {
                        "id": "create",
                        "description": "Create file 'a.txt'",
                        "executor": "CODER",
                        "dependencies": [],
                    },
                ],
            }
            return {"success": True, "action_json": plan}
        if role == AIRole.SUPERVISOR:
            return {
                "success": True,
                "action_json": {"decision": "proceed", "reason": "test stub"},
            }
        return {"success": False, "error": "No dummy response for role"}


@pytest.mark.asyncio
async def test_auto_plan_generates_and_executes(tmp_path: Path):
    sandbox = LocalUnsafeSandbox(project_dir=str(tmp_path))
    tools = AgentTools(sandbox_manager=sandbox)
    agent = PlannerStub()
    config = {"output_dir": str(tmp_path), "auto_plan": True, "continue_on_failure": True}
    orch = Orchestrator(agent=agent, tools=tools, config=config)
    
    res = await orch.execute_full_pipeline("a user request", output_dir=str(tmp_path))
    
    # План должен быть выполнен, но задача CODER провалится, так как заглушка не реализует его.
    # Важно, что нет ошибки 'execution_plan_missing'.
    assert res.get("error") is None
    assert res["success"] is False # Ожидаем провал из-за отсутствия CODER
    
    # Проверяем, что задача была в плане и провалилась, как ожидалось
    tasks = res.get("tasks") or []
    assert len(tasks) == 1
    task_result = tasks[0]
    assert task_result["id"] == "create"
    assert task_result["success"] is False
    assert task_result["error"] == "No dummy response for role"
