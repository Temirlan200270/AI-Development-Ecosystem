import pytest

from temir.core.models import AIRole


@pytest.mark.asyncio
async def test_tester_agent_without_api_returns_error(monkeypatch):
    from temir.agents.tester_agent import TesterAgent as TesterAgentImpl

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    agent = TesterAgentImpl(api_key=None, tools=None, rate_limiter=None, prompts_data={})
    res = await agent.execute_task("Run tests", AIRole.TESTER, context=None)
    assert res["success"] is False
    assert res.get("error")
    await agent.close()
