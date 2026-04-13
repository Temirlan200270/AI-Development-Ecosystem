"""
Специализированный агент для роли SUPERVISOR.
Использует Gemini API для принятия решений о дальнейших действиях.
"""

import ast
import asyncio
import json
import logging
import os
import platform
import re
from pathlib import Path
from typing import Any, Dict, Optional, List

import httpx
import yaml

from temir.core.models import AIRole
from temir.tools.agent_tools import AgentTools
from .base_agent import BaseAgent

try:
    import google.generativeai as genai
except ImportError:
    genai = None

logger = logging.getLogger(__name__)

class SupervisorAgent(BaseAgent):
    """
    Агент, управляющий Gemini API для роли SUPERVISOR.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        tools: Optional[AgentTools] = None,
        rate_limiter: Optional[Any] = None,
        prompts_data: Optional[Dict[str, Any]] = None,
    ):
        self.tools_instance = tools # SUPERVISOR не использует инструменты напрямую
        self.rate_limiter = rate_limiter
        self._gemini_configured = False
        self.prompts_data = prompts_data or {}
        
        self._http_client = httpx.AsyncClient(timeout=60.0)

        if not genai:
            logger.warning("google-generativeai не установлен для SupervisorAgent.")
        else:
            effective_api_key = api_key or os.getenv("GEMINI_API_KEY")
            if effective_api_key:
                try:
                    genai.configure(api_key=effective_api_key.strip())
                    self._gemini_configured = True
                    logger.info("Gemini (цепочка моделей) для SupervisorAgent.")
                except Exception as e:
                    logger.error(f"Ошибка инициализации Gemini для SupervisorAgent: {e}")
            else:
                logger.warning("GEMINI_API_KEY отсутствует для SupervisorAgent.")

    async def close(self):
        """Закрывает HTTP-клиент."""
        if self._http_client:
            await self._http_client.aclose()

    def _get_role_prompt(
        self,
        role: AIRole,
        task_description: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        role_key = role.value.lower()
        
        prompt_template = None
        if self.prompts_data:
            role_config = self.prompts_data.get("roles", {}).get(role_key, {})
            prompt_type = (context or {}).get("prompt_type", "default")
            prompt_template = role_config.get("prompts", {}).get(prompt_type)
            
            common_rules = self.prompts_data.get("common", {}).get("json_rules", "")
            if prompt_template and "<<json_rules>>" in prompt_template:
                prompt_template = prompt_template.replace("<<json_rules>>", common_rules)

        if not prompt_template:
            logger.warning(f"Fallback prompt for {role}.")
            prompt_template = (
                f"You are a {role.value}. A task failed. Decide what to do next. "
                "Respond with JSON: {'decision': 'rollback'|'proceed'}"
            )

        replacements = {
            "{{task_description}}": task_description,
            "{{failed_task}}": (context or {}).get("failed_task", "N/A"),
            "{{error_message}}": (context or {}).get("error_message", "N/A"),
        }

        final_prompt = prompt_template
        for key, value in replacements.items():
            final_prompt = final_prompt.replace(key, str(value))

        return final_prompt

    def _extract_retry_delay(self, error_message: str) -> float:
        match = re.search(r"retry in ([\d.]+)s", error_message, re.IGNORECASE)
        if match: return float(match.group(1))
        match = re.search(r"retry_delay\s*\{[^}]*seconds:\s*(\d+)", error_message, re.IGNORECASE)
        if match: return float(match.group(1))
        return 15.0

    def _safe_json_loads(self, text: str) -> Dict[str, Any]:
        """Улучшенный парсер JSON."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        try:
            candidate = self._extract_json_object(text)
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass
            
        try:
            candidate = self._extract_json_object(text)
            text_for_eval = candidate.replace("true", "True").replace("false", "False").replace("null", "None")
            return ast.literal_eval(text_for_eval)
        except Exception as e:
            raise json.JSONDecodeError(f"Parsing failed final attempt: {e}", text, 0)

    async def execute_task(
        self,
        task_description: str,
        role: AIRole,
        context: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Выполняет задачу, используя Gemini API для роли SUPERVISOR.
        """
        if role != AIRole.SUPERVISOR:
            logger.error(
                f"SupervisorAgent предназначен только для роли SUPERVISOR, получена {role}"
            )
            return {"success": False, "error": "Неподдерживаемая роль для SupervisorAgent."}

        if not self._gemini_configured:
            return {"success": False, "error": "Gemini Model не инициализирован для SupervisorAgent."}

        prompt_content = self._get_role_prompt(role, task_description, context)

        from temir.llm.kernel import get_llm_kernel

        retry_count = 0
        while retry_count < max_retries:
            try:
                res = await get_llm_kernel().generate_gemini(
                    prompt_content,
                    rate_limiter=self.rate_limiter,
                    role_hint=role.value,
                    task_id="",
                )
                if not res.success:
                    raise RuntimeError(res.error or "llm kernel failed")

                text = res.text
                usage = res.usage
                billing_model = res.billing_model

                action_json = self._safe_json_loads(text)
                if "decision" not in action_json:
                    raise json.JSONDecodeError("Missing 'decision' field", str(action_json), 0)

                return {
                    "success": True,
                    "action_json": action_json,
                    "usage": usage,
                    "billing_model": billing_model,
                }

            except json.JSONDecodeError as e:
                logger.warning(
                    f"Invalid JSON from Gemini (Supervisor). Error: {e}. Retrying... (attempt {retry_count+1})",
                )
                retry_count += 1
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Gemini Error (Supervisor) ({retry_count+1}): {e}")
                await asyncio.sleep(2**retry_count)
                retry_count += 1
        
        return {"success": False, "error": "SupervisorAgent max retries exceeded."}

    def _extract_json_object(self, text: str) -> str:
        if not text: raise json.JSONDecodeError("Empty", "", 0)
        match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
        if match: return match.group(1).strip()
        
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{": depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0: return text[start : i + 1]
        raise json.JSONDecodeError("No JSON object found", text, 0)
