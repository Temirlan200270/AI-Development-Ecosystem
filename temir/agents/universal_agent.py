"""
Universal AI Agent for Temir CLI, acting as a dispatcher.
It delegates tasks to specialized agents based on their assigned role.
"""

import ast
import asyncio
import json
import logging
import os
import platform
import re
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import yaml

from temir.core.models import AIRole
from temir.tools.agent_tools import AgentTools
from .base_agent import BaseAgent
from .backend_coder_agent import BackendCoderAgent
from .gemini_enhancer_agent import GeminiEnhancerAgent
from .system_architect_agent import SystemArchitectAgent
from .tester_agent import TesterAgent
from .supervisor_agent import SupervisorAgent

logger = logging.getLogger(__name__)


class UniversalAgent(BaseAgent):
    """
    Агент-диспетчер, который направляет запросы к специализированным агентам
    на основе их ролей.
    """

    def __init__(
        self,
        backend_coder_agent: BackendCoderAgent,
        system_architect_agent: SystemArchitectAgent,
        tester_agent: TesterAgent,
        reviewer_agent: GeminiEnhancerAgent,
        supervisor_agent: SupervisorAgent,
    ):
        self.backend_coder_agent = backend_coder_agent
        self.system_architect_agent = system_architect_agent
        self.tester_agent = tester_agent
        self.reviewer_agent = reviewer_agent
        self.supervisor_agent = supervisor_agent
        logger.info("UniversalAgent (Dispatcher) инициализирован.")

    async def close(self):
        """Закрывает все специализированные агенты."""
        await self.backend_coder_agent.close()
        await self.system_architect_agent.close()
        await self.tester_agent.close()
        await self.reviewer_agent.close()
        await self.supervisor_agent.close()

    # Эти методы теперь не нужны в диспетчере, так как логика промптов
    # и инструментов перенесена в специализированные агенты.
    # def _get_tools_description(self) -> str: pass
    # def _get_role_prompt(self, role: AIRole, task_description: str, context: Optional[Dict[str, Any]] = None) -> str: pass
    # async def _call_mercury_api(self, messages: list, max_tokens: int = 4096) -> Dict[str, Any]: pass

    async def execute_task(
        self,
        task_description: str,
        role: AIRole,
        context: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Делегирует выполнение задачи соответствующему специализированному агенту.
        """
        if role == AIRole.BACKEND_CODER:
            return await self.backend_coder_agent.execute_task(
                task_description, role, context, max_retries
            )
        elif role == AIRole.CODER:
            return await self.backend_coder_agent.execute_task(
                task_description,
                AIRole.BACKEND_CODER,
                context,
                max_retries,
            )
        elif role == AIRole.PLANNER:
            return await self.system_architect_agent.execute_task(
                task_description,
                AIRole.SYSTEM_ARCHITECT,
                context,
                max_retries,
            )
        elif role == AIRole.SYSTEM_ARCHITECT:
            return await self.system_architect_agent.execute_task(
                task_description, role, context, max_retries
            )
        elif role == AIRole.TESTER:
            return await self.tester_agent.execute_task(
                task_description, role, context, max_retries
            )
        elif role == AIRole.REVIEWER:
            return await self.reviewer_agent.execute_task(
                task_description, role, context, max_retries
            )
        elif role == AIRole.SUPERVISOR:
            return await self.supervisor_agent.execute_task(
                task_description, role, context, max_retries
            )
        else:
            logger.error(f"UniversalAgent: Неизвестная или неподдерживаемая роль: {role.value}")
            return {"success": False, "error": f"Неизвестная или неподдерживаемая роль: {role.value}"}
