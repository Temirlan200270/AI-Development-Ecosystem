import json
from pathlib import Path

import pytest

from temir.core.models import AIRole
from temir.core.orchestrator import Orchestrator
from temir.main import save_results
from temir.sandbox.local_sandbox import LocalUnsafeSandbox
from temir.tools.agent_tools import AgentTools


class SnapshotDummyAgent:
    def __init__(self):
        self.calls = 0

    async def execute_task(self, task_description, role, context=None, max_retries=3):
        self.calls += 1
        if role == AIRole.SYSTEM_ARCHITECT:
            return {
                "success": True,
                "action_json": {
                    "execution_plan": [
                        {
                            "id": "a",
                            "description": "run echo",
                            "executor": "CODER",
                            "dependencies": [],
                        },
                    ],
                },
            }
        if role == AIRole.CODER:
            return {
                "success": True,
                "action_json": {
                    "action": "execute_shell",
                    "args": {"command": "echo ok"},
                },
            }
        if role == AIRole.SUPERVISOR:
            return {
                "success": True,
                "action_json": {"decision": "proceed", "reason": "test"},
            }
        return {"success": False, "error": "unexpected role"}


@pytest.mark.asyncio
async def test_execution_results_snapshot(tmp_path: Path):
    sandbox = LocalUnsafeSandbox(project_dir=str(tmp_path))
    tools = AgentTools(sandbox_manager=sandbox)
    agent = SnapshotDummyAgent()
    config = {
        "output_dir": str(tmp_path),
        "collect_artifacts": False,
        "continue_on_failure": False,
        "cache_enabled": False,
    }
    orch = Orchestrator(agent=agent, tools=tools, config=config)

    result = await orch.execute_full_pipeline("snap run", output_dir=str(tmp_path))
    save_results(result, str(tmp_path))

    results_path = tmp_path / "execution_results.json"
    assert results_path.exists()
    data = json.loads(results_path.read_text(encoding="utf-8"))
    assert "success" in data
    assert "summary" in data
    assert isinstance(data["summary"], dict)
    assert "tasks" in data and isinstance(data["tasks"], list)
    for key in ["total_tasks", "completed", "failed", "start_time", "end_time"]:
        assert key in data["summary"]
