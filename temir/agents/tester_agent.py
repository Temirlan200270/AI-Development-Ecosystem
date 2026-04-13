"""
Специализированный агент для роли TESTER.
Использует Gemini API для анализа и выполнения задач тестирования.
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

class TesterAgent(BaseAgent):
    """
    Агент, управляющий Gemini API для роли TESTER.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        tools: Optional[AgentTools] = None,
        rate_limiter: Optional[Any] = None,
        prompts_data: Optional[Dict[str, Any]] = None,
    ):
        self.tools_instance = tools
        self.rate_limiter = rate_limiter
        self._gemini_configured = False
        self.prompts_data = prompts_data or {}
        
        self._http_client = httpx.AsyncClient(timeout=60.0)

        if not genai:
            logger.warning("google-generativeai не установлен для TesterAgent.")
        else:
            effective_api_key = api_key or os.getenv("GEMINI_API_KEY")
            if effective_api_key:
                try:
                    genai.configure(api_key=effective_api_key.strip())
                    self._gemini_configured = True
                    logger.info("Gemini (цепочка моделей) для TesterAgent.")
                except Exception as e:
                    logger.error(f"Ошибка инициализации Gemini для TesterAgent: {e}")
            else:
                logger.warning("GEMINI_API_KEY отсутствует для TesterAgent.")

    async def close(self):
        """Закрывает HTTP-клиент (если используется)."""
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
                f"You are {role.value}. Perform: {{task_description}}\n"
                f"Tools: {{tools_description}}\n"
                "Respond with JSON only: { 'action': ..., 'args': ... }"
            )

        replacements = {
            "{{task_description}}": task_description,
            "{{tools_description}}": self._get_tools_description(),
            "{{platform_info}}": platform.system(),
            "{{python_cmd}}": "python" if platform.system() == "Windows" else "python3",
            "{{file_to_review}}": (context or {}).get("file_to_review", "NO CODE PROVIDED"),
            "{{source_code_hint}}": (context or {}).get("source_code_to_test", ""),
        }

        final_prompt = prompt_template
        for key, value in replacements.items():
            final_prompt = final_prompt.replace(key, str(value))

        return final_prompt

    def _get_tools_description(self) -> str:
        """Инструменты, доступные для TesterAgent."""
        return (
            "AVAILABLE TOOLS:\n"
            "1. run_tests(path): Run pytest test suite.\n"
            "2. run_linter(path): Run ruff linter.\n"
            "3. execute_shell(command): Run bash/powershell commands.\n"
            "4. read_file(path): Read file content.\n"
            "5. write_file(path, content): Create/Overwrite file.\n"
            "6. install_package(package_name): pip install package.\n"
            "7. list_directory(dir_path): List directory contents.\n"
            "8. git_status(): Check Git repository status.\n"
        )

    def _extract_retry_delay(self, error_message: str) -> float:
        match = re.search(r"retry in ([\d.]+)s", error_message, re.IGNORECASE)
        if match: return float(match.group(1))
        match = re.search(r"retry_delay\s*\{{[^}]*seconds:\s*(\d+)", error_message, re.IGNORECASE)
        if match: return float(match.group(1))
        return 15.0 # Дефолтная задержка

    def _safe_json_loads(self, text: str) -> Dict[str, Any]:
        """Улучшенный парсер JSON."""
        # 1. Чистый JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. Извлечение из Markdown
        try:
            candidate = self._extract_json_object(text)
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass
            
        # 3. "Грязный" хак для Python-словарей (если модель вернула {key: 'val'})
        try:
            candidate = self._extract_json_object(text)
            # Заменяем литералы JS/JSON на Python
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
        Выполняет задачу, используя Gemini API для роли TESTER.
        """
        if role != AIRole.TESTER:
            logger.error(
                f"TesterAgent предназначен только для роли TESTER, получена {role}"
            )
            return {"success": False, "error": "Неподдерживаемая роль для TesterAgent."}

        if not self._gemini_configured:
            return {"success": False, "error": "Gemini Model не инициализирован для TesterAgent."}

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

                if "action" not in action_json:
                    raise json.JSONDecodeError("Missing 'action' field", str(action_json), 0)

                return {
                    "success": True,
                    "action_json": action_json,
                    "usage": usage,
                    "billing_model": billing_model,
                }

            except json.JSONDecodeError:
                logger.warning(
                    f"Invalid JSON from Gemini (TesterAgent). Retrying... (attempt {retry_count+1})",
                )
                retry_count += 1
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Gemini Error (TesterAgent) ({retry_count+1}): {e}")
                await asyncio.sleep(2**retry_count)
                retry_count += 1
        
        return {"success": False, "error": "TesterAgent max retries exceeded."}

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
