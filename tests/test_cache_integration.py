from pathlib import Path

import pytest

from temir.core.models import AIRole
from temir.core.orchestrator import Orchestrator
from temir.memory.cache_manager import CacheManager
from temir.sandbox.local_sandbox import LocalUnsafeSandbox
from temir.tools.agent_tools import AgentTools


class CacheTestAgent:
    """Архитектор + счётчик вызовов кодера (для проверки кэша)."""

    def __init__(self, action_json: dict):
        self.action_json = action_json
        self.calls = 0

    async def execute_task(
        self,
        task_description,
        role,
        context=None,
        max_retries=3,
    ):
        if role == AIRole.SYSTEM_ARCHITECT:
            return {
                "success": True,
                "action_json": {
                    "execution_plan": [
                        {
                            "id": "t1",
                            "description": "write cached",
                            "executor": "CODER",
                            "dependencies": [],
                        },
                    ],
                },
            }
        if role == AIRole.CODER:
            self.calls += 1
            return {"success": True, "action_json": self.action_json}
        if role == AIRole.SUPERVISOR:
            return {
                "success": True,
                "action_json": {"decision": "proceed", "reason": "test"},
            }
        return {"success": False, "error": f"No stub for {role}"}


def build_orchestrator(
    tmpdir: Path,
    agent: CacheTestAgent,
    cache_db: Path,
) -> Orchestrator:
    sandbox = LocalUnsafeSandbox(project_dir=str(tmpdir))
    tools = AgentTools(sandbox_manager=sandbox)
    cache = CacheManager(db_path=str(cache_db))
    config = {
        "output_dir": str(tmpdir),
        "cache_enabled": True,
        "continue_on_failure": False,
        "collect_artifacts": False,
    }
    orch = Orchestrator(
        agent=agent,
        tools=tools,
        cache_manager=cache,
        config=config,
    )
    return orch


@pytest.mark.asyncio
async def test_cache_hit_on_second_run(tmp_path: Path):
    action = {"action": "write_file", "args": {"path": "cached.txt", "content": "ok"}}
    agent = CacheTestAgent(action_json=action)
    cache_db = tmp_path / "cache.db"

    orch1 = build_orchestrator(tmp_path, agent, cache_db)
    res1 = await orch1.execute_full_pipeline("build T", output_dir=str(tmp_path))
    assert res1["success"] is True
    # архитектор + кодер (первый раз без кэша для кодера)
    assert agent.calls == 1
    assert res1["summary"]["cache_hits"] == 0
    assert res1["summary"]["cache_misses"] >= 1

    orch2 = build_orchestrator(tmp_path, agent, cache_db)
    res2 = await orch2.execute_full_pipeline("build T", output_dir=str(tmp_path))
    assert res2["success"] is True
    assert agent.calls == 1
    assert res2["summary"]["cache_hits"] >= 1
