"""
Специализированный агент для роли REVIEWER (Gemini Enhancer).
Использует Gemini API для анализа, рефакторинга и улучшения кода.
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

class GeminiEnhancerAgent(BaseAgent):
    """
    Агент, управляющий Gemini API для роли REVIEWER.
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
        
        self._http_client = httpx.AsyncClient(timeout=120.0) # Увеличенный таймаут для ревью

        if not genai:
            logger.warning("google-generativeai не установлен для GeminiEnhancerAgent.")
        else:
            effective_api_key = api_key or os.getenv("GEMINI_API_KEY")
            if effective_api_key:
                try:
                    genai.configure(api_key=effective_api_key.strip())
                    self._gemini_configured = True
                    logger.info(
                        "Gemini (цепочка моделей) для GeminiEnhancerAgent.",
                    )
                except Exception as e:
                    logger.error(f"Ошибка инициализации Gemini для GeminiEnhancerAgent: {e}")
            else:
                logger.warning("GEMINI_API_KEY отсутствует для GeminiEnhancerAgent.")

    async def close(self):
        """Закрывает HTTP-клиент."""
        if self._http_client:
            await self._http_client.aclose()

    def _get_tools_description(self) -> str:
        # Инструменты, доступные для GeminiEnhancerAgent
        return (
            "AVAILABLE TOOLS:\n"
            "1. write_file(path, content): Create/Overwrite file.\n"
            "2. smart_patch(path, patch_text): Apply a patch to a file using fuzzy matching.\n"
            "3. execute_shell(command): Run bash/powershell commands.\n"
            "4. read_file(path): Read file content.\n"
            "5. list_directory(path): ls/dir command.\n"
            "6. create_directory(dir_path): mkdir -p (supports 'path' arg too).\n"
            "7. run_tests(path): Run pytest suite.\n"
            "8. run_linter(path): Run ruff linter.\n"
            "9. install_package(package_name): pip install.\n"
            "10. git_init(): Initialize a new Git repository.\n"
            "11. git_add(files: List[str]): Stages specified files.\n"
            "12. git_commit(message: str): Commits staged changes.\n"
            "13. git_status(): Checks the status of the repository.\n"
            "14. git_diff(): Shows changes between working directory and staging area."
        )

    def _get_role_prompt(
        self,
        role: AIRole,
        task_description: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        role_key = "gemini_enhancer" # Эта роль в prompts.yaml
        
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
                f"You are {role.value}. Your task is to review code. {{file_to_review}}\n"
                "If fixing, respond with JSON only: { 'action': 'smart_patch', 'args': {'path': '...', 'patch_text': '...'} }"
            )

        # Ограничиваем размер кода для ревью (Gemini может обработать больше, но ограничим для стабильности)
        file_to_review = (context or {}).get("file_to_review", "")
        if file_to_review and len(file_to_review) > 8000:  # Ограничение ~8000 символов
            logger.warning(f"Код файла для ревью слишком большой ({len(file_to_review)} символов). Обрезаю до 8000.")
            file_to_review = file_to_review[:8000] + "\n\n... [код обрезан для экономии токенов, используйте read_file для полного кода]"
        
        replacements = {
            "{{task_description}}": task_description,
            "{{tools_description}}": self._get_tools_description(),
            "{{file_to_review}}": file_to_review if file_to_review else "NO CODE PROVIDED",
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
            
        # 3. "Грязный" хак для Python-словарей
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
        Выполняет задачу, используя Gemini API для роли REVIEWER.
        """
        if role != AIRole.REVIEWER:
            logger.error(
                f"GeminiEnhancerAgent предназначен только для роли REVIEWER, получена {role}"
            )
            return {"success": False, "error": "Неподдерживаемая роль для GeminiEnhancerAgent."}

        if not self._gemini_configured:
            return {"success": False, "error": "Gemini Model не инициализирован для GeminiEnhancerAgent."}

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

                # Если задача - просто ревью, то мы ожидаем текстовый ответ
                if "review" in task_description.lower() and "fix" not in task_description.lower():
                    return {
                        "success": True,
                        "output_text": text,
                        "usage": usage,
                        "billing_model": billing_model,
                    }

                action_json = self._safe_json_loads(text)
                if "action" not in action_json:
                    raise json.JSONDecodeError("Missing 'action' field", str(action_json), 0)

                return {
                    "success": True,
                    "action_json": action_json,
                    "usage": usage,
                    "billing_model": billing_model,
                }

            except json.JSONDecodeError as e:
                logger.warning(
                    f"Invalid JSON from Gemini (Enhancer) or expected text. Error: {e}. Retrying... (attempt {retry_count+1})",
                )
                retry_count += 1
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Gemini Error (Enhancer) ({retry_count+1}): {e}")
                await asyncio.sleep(2**retry_count)
                retry_count += 1
        
        return {"success": False, "error": "GeminiEnhancerAgent max retries exceeded."}

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
