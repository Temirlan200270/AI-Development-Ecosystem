"""
Специализированный агент для роли BACKEND_CODER.
Использует Mercury Coder API для генерации кода и команд.
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

class BackendCoderAgent(BaseAgent):
    """
    Агент, управляющий Mercury Coder API.
    Предназначен для роли BACKEND_CODER.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        tools: Optional[AgentTools] = None,
        rate_limiter: Optional[Any] = None,
        prompts_data: Optional[Dict[str, Any]] = None,
        gemini_api_key: Optional[str] = None,
    ):
        self.tools_instance = tools
        self.rate_limiter = rate_limiter
        self.mercury_api_key = None
        self._gemini_fallback_ready = False
        self.prompts_data = prompts_data or {}
        
        self._http_client = httpx.AsyncClient(timeout=60.0)

        # Инициализация Mercury API
        raw_mercury_key = api_key or os.getenv("INCEPTION_API_KEY")
        self.mercury_api_key = raw_mercury_key.strip() if raw_mercury_key else None
        if not self.mercury_api_key:
            logger.warning("INCEPTION_API_KEY отсутствует для BackendCoderAgent.")
        else:
            logger.info("Mercury Coder доступен для BackendCoderAgent.")

        # Инициализация Gemini как fallback
        if genai:
            effective_gemini_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
            if effective_gemini_key:
                try:
                    genai.configure(api_key=effective_gemini_key.strip())
                    self._gemini_fallback_ready = True
                    logger.info(
                        "Gemini (цепочка моделей) как fallback для BackendCoderAgent.",
                    )
                except Exception as e:
                    logger.warning(f"Не удалось инициализировать Gemini fallback: {e}")
            else:
                logger.warning("GEMINI_API_KEY отсутствует для fallback в BackendCoderAgent.")
        else:
            logger.warning("google-generativeai не установлен. Fallback на Gemini недоступен.")

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
                f"You are {role.value}. Perform: {{task_description}}\n"
                f"Tools: {{tools_description}}\n"
                "Respond with JSON only: { 'action': ..., 'args': ... }"
            )

        # Для Mercury API НЕ отправляем большие контекстные данные (код файлов)
        # Mercury ожидает простой промпт без больших объемов кода
        # Если нужен код файла, агент должен использовать read_file инструмент
        
        replacements = {
            "{{task_description}}": task_description,
            "{{tools_description}}": self._get_tools_description(),
            "{{platform_info}}": platform.system(),
            "{{python_cmd}}": "python" if platform.system() == "Windows" else "python3",
            # НЕ включаем file_to_review и source_code_hint для Mercury - они могут быть очень большими
            # Агент должен использовать read_file если нужен код
            "{{file_to_review}}": "",  # Пусто для Mercury
            "{{source_code_hint}}": "",  # Пусто для Mercury
        }

        final_prompt = prompt_template
        for key, value in replacements.items():
            final_prompt = final_prompt.replace(key, str(value))

        return final_prompt
        
    def _get_tools_description(self) -> str:
        # Инструменты, доступные для BackendCoderAgent
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

    async def _call_mercury_api(self, messages: List[Dict[str, str]], max_tokens: int = 4096) -> Dict[str, Any]:
        """Внутренний метод для вызова Mercury Coder API."""
        if not self.mercury_api_key:
            return {"success": False, "error": "INCEPTION_API_KEY missing."}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.mercury_api_key}",
        }
        json_payload = {
            "model": "mercury-2",
            "messages": messages,
            "max_tokens": max_tokens,
        }
        
        if self.rate_limiter:
            await self.rate_limiter.acquire()

        try:
            response = await self._http_client.post(
                "https://api.inceptionlabs.ai/v1/chat/completions",
                headers=headers,
                json=json_payload,
            )
            response.raise_for_status()
            data = response.json()

            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"]["content"]
                if not content or not content.strip():
                    return {"success": False, "error": "Mercury returned empty response (Blank string)."}
                
                return {"success": True, "content": content}
            
            return {"success": False, "error": "Empty response structure from Mercury."}

        except httpx.HTTPStatusError as e:
            # Обработка HTTP ошибок (503, 429, и т.д.)
            status_code = e.response.status_code if hasattr(e, 'response') else None
            if status_code == 503:
                logger.warning(f"Mercury API недоступен (503). Будет использован fallback.")
                return {"success": False, "error": "Service Unavailable - will use fallback"}
            elif status_code == 429:
                logger.warning(f"Mercury API rate limit (429). Будет использован fallback.")
                return {"success": False, "error": "Rate limit exceeded - will use fallback"}
            else:
                logger.error(f"Mercury API HTTP Error {status_code}: {e}")
                return {"success": False, "error": f"HTTP {status_code}: {str(e)}"}
        except httpx.RequestError as e:
            logger.error(f"Mercury API Request Error: {e}")
            return {"success": False, "error": str(e)}

    async def _call_gemini_fallback(self, prompt_content: str) -> Dict[str, Any]:
        """Fallback: цепочка Gemini, если Mercury не отвечает."""
        if not self._gemini_fallback_ready:
            return {"success": False, "error": "Gemini fallback не доступен."}

        from temir.llm.kernel import get_llm_kernel

        res = await get_llm_kernel().generate_gemini(
            prompt_content,
            rate_limiter=self.rate_limiter,
            role_hint="BACKEND_CODER",
            task_id="",
        )
        if not res.success:
            return {"success": False, "error": res.error or "llm kernel failed"}
        return {
            "success": True,
            "content": res.text,
            "usage": res.usage,
            "billing_model": res.billing_model,
        }

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

    async def execute_task(
        self,
        task_description: str,
        role: AIRole,
        context: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Выполняет задачу, используя Mercury Coder API.
        """
        if role != AIRole.BACKEND_CODER:
            logger.error(
                f"BackendCoderAgent предназначен только для роли BACKEND_CODER, получена {role}"
            )
            return {"success": False, "error": "Неподдерживаемая роль для BackendCoderAgent."}

        prompt_content = self._get_role_prompt(role, task_description, context)
        messages = [{"role": "user", "content": prompt_content}]
        
        # Оценка входных токенов
        input_tokens = int(len(prompt_content) / 4)

        retry_count = 0
        while retry_count < max_retries:
            mercury_resp = await self._call_mercury_api(messages)

            if mercury_resp["success"]:
                content = mercury_resp["content"]
                logger.info(f"🤖 Mercury Output: {content[:100]}...")
                
                try:
                    action_json = self._safe_json_loads(content)
                    
                    # Оценка выходных токенов
                    output_tokens = int(len(content) / 4)
                    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}

                    return {"success": True, "action_json": action_json, "usage": usage}
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from Mercury (attempt {retry_count}). Retrying...")
                    retry_count += 1
                    await asyncio.sleep(1) # Добавляем небольшую задержку при невалидном JSON
            else:
                logger.warning(f"Mercury Error: {mercury_resp.get('error')} (attempt {retry_count})")
                retry_count += 1
                await asyncio.sleep(1) # Добавляем небольшую задержку при ошибке API
        
        # Если Mercury не ответил после всех попыток или вернул критическую ошибку, используем Gemini как fallback
        last_error = mercury_resp.get('error', '') if isinstance(mercury_resp, dict) else str(mercury_resp)
        should_use_fallback = (
            "Service Unavailable" in last_error or 
            "Rate limit" in last_error or
            "empty response" in last_error.lower() or
            "blank string" in last_error.lower()
        )
        
        if should_use_fallback:
            logger.warning(f"Mercury недоступен или не отвечает ({last_error[:50]}...). Переключаюсь на Gemini fallback...")
        else:
            logger.warning("Mercury не ответил после всех попыток. Переключаюсь на Gemini fallback...")
        
        gemini_resp = await self._call_gemini_fallback(prompt_content)
        
        if gemini_resp["success"]:
            content = gemini_resp["content"]
            logger.info(f"🤖 Gemini Fallback Output: {content[:100]}...")
            
            try:
                action_json = self._safe_json_loads(content)
                usage = gemini_resp.get("usage", {"input_tokens": input_tokens, "output_tokens": int(len(content) / 4)})
                out = {
                    "success": True,
                    "action_json": action_json,
                    "usage": usage,
                    "fallback_used": "gemini",
                }
                if gemini_resp.get("billing_model"):
                    out["billing_model"] = gemini_resp["billing_model"]
                return out
            except json.JSONDecodeError as e:
                logger.error(f"Gemini fallback вернул невалидный JSON: {e}")
                return {"success": False, "error": f"Gemini fallback failed: Invalid JSON. {str(e)}"}
        else:
            return {"success": False, "error": f"Mercury max retries exceeded. Gemini fallback also failed: {gemini_resp.get('error')}"}