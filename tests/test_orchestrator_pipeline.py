import pytest
from pathlib import Path

from temir.core.models import AIRole
from temir.core.orchestrator import Orchestrator
from temir.sandbox.local_sandbox import LocalUnsafeSandbox
from temir.tools.agent_tools import AgentTools


class DummyAgent:
    def __init__(self, responses):
        # responses: dict[role, callable(task_description, context)->action_json или текст]
        self.responses = responses
        self.calls = 0

    async def execute_task(self, task_description, role, context, _max_retries=3):
        self.calls += 1
        if role == AIRole.REVIEWER:
            return {"success": True, "output_text": "Code looks good. No issues found."}
        fn = self.responses.get(role)
        if fn is None:
            return {"success": False, "error": "No dummy response for role"}
        action_json = fn(task_description, context)
        return {"success": True, "action_json": action_json}


def write_spec(tmpdir: Path, content: str) -> Path:
    spec = tmpdir / "spec.yaml"
    spec.write_text(content, encoding="utf-8")
    return spec


def build_orchestrator(tmpdir: Path, agent: DummyAgent) -> Orchestrator:
    sandbox = LocalUnsafeSandbox(project_dir=str(tmpdir))
    tools = AgentTools(sandbox_manager=sandbox)
    config = {"output_dir": str(tmpdir)}
    orch = Orchestrator(agent=agent, tools=tools, config=config)
    # отключаем артефакты для тестов
    orch.config["collect_artifacts"] = False
    orch.config["continue_on_failure"] = False
    return orch


@pytest.mark.asyncio
async def test_pipeline_success(tmp_path: Path):
    # Агент пишет файл
    def coder_resp(_desc, _ctx):
        return {"action": "write_file", "args": {"path": "hello.txt", "content": "hi"}}

    # Ответ Архитектора
    def architect_resp(_desc, _ctx):
        return {
            "execution_plan": [
                {
                    "id": "t1",
                    "description": "create hello",
                    "executor": "CODER",
                    "dependencies": [],
                }
            ]
        }

    agent = DummyAgent({AIRole.CODER: coder_resp, AIRole.SYSTEM_ARCHITECT: architect_resp})
    orch = build_orchestrator(tmp_path, agent)

    res = await orch.execute_full_pipeline("some request", output_dir=str(tmp_path))
    assert res["success"] is True
    assert res.get("run_id")
    assert res["summary"].get("run_id") == res["run_id"]
    assert res["summary"]["total_tasks"] == 1
    assert res["summary"]["completed"] == 1
    assert res["summary"]["failed"] == 0
    assert (tmp_path / "hello.txt").exists()


@pytest.mark.asyncio
async def test_pipeline_skip_by_dependencies(tmp_path: Path):
    def coder_resp(_desc, _ctx):
        return {"action": "write_file", "args": {"path": "file.txt", "content": "x"}}

    def architect_resp(_desc, _ctx):
        return {
            "execution_plan": [
                {
                    "id": "b",
                    "description": "write file",
                    "executor": "CODER",
                    "dependencies": ["a"],
                }
            ]
        }

    agent = DummyAgent({AIRole.CODER: coder_resp, AIRole.SYSTEM_ARCHITECT: architect_resp})
    orch = build_orchestrator(tmp_path, agent)

    res = await orch.execute_full_pipeline("some request", output_dir=str(tmp_path))
    assert res["success"] is False  # Пайплайн не считается успешным, если задача пропущена из-за зависимостей
    assert res["summary"]["total_tasks"] == 0
    assert any(t.get("skipped") for t in res["tasks"])


@pytest.mark.asyncio
async def test_pipeline_empty_plan(tmp_path: Path):
    def architect_resp(_desc, _ctx):
        return {"execution_plan": []}

    agent = DummyAgent({AIRole.SYSTEM_ARCHITECT: architect_resp})
    orch = build_orchestrator(tmp_path, agent)
    res = await orch.execute_full_pipeline("some request", output_dir=str(tmp_path))
    assert res["success"] is False
    assert "SYSTEM_ARCHITECT generated a plan, but all tasks were invalid or the plan was empty." in res["error"]
