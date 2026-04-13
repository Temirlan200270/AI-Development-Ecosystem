"""
Core Orchestrator for Temir CLI. (v_quality_pipeline_final)
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .action_preflight import (
    ActionPreflightViolation,
    preflight_repair_context_message,
    preflight_tool_steps,
)
from .capabilities import (
    CapabilityDeniedError,
    authorize_plan_steps,
    capabilities_required_for_action,
    resolve_allowed_capabilities,
)
from .execution_gate import (
    can_execute_tool_step,
    register_successful_intent,
)
from .level_validation import LevelCompletionError, validate_level_completion
from .retry_policy import ir_contract_error_retryable, preflight_violation_retryable
from .step_audit import compute_step_intent_sha256
from .execution_graph import (
    execution_levels_for_plan,
    level_allows_parallel_gather,
)
from .ir_v3 import (
    IRV3ContractError,
    compile_llm_json_to_execution_plan_v3,
    plan_to_executor_dicts,
)
from .models import AIRole, ExecutionState, Task
from .platform_context import platform_event_fields, resolve_platform_context
from .tool_registry import ToolRegistry
from .snapshot_manager import SnapshotManager
from .cost_calculator import CostCalculator
from temir.web.event_envelope import TraceContext, envelope_now
from temir.web.pipeline_events import publish_pipeline_event, summarize_tool_action
from temir.web.run_telemetry import (
    attach_pipeline_run,
    current_run,
    detach_pipeline_run,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """Оркестратор, который управляет пайплайном качества."""

    def __init__(self, **kwargs):
        self.config = kwargs.get("config")
        self.agent = kwargs.get("agent")
        self.tools = kwargs.get("tools")
        self.cache = kwargs.get("cache_manager")
        self.spec_parser = kwargs.get("spec_parser")
        if not all([self.config, self.agent, self.tools]):
            raise ValueError("Orchestrator requires 'config', 'agent', and 'tools'")
        self.execution_state = ExecutionState()
        self._cache_hits = 0
        self._cache_misses = 0
        self.snapshot_manager = None
        self.cost_calculator = CostCalculator()
        self._tool_registry = ToolRegistry.from_tools(self.tools)
        self._allowed_capabilities = resolve_allowed_capabilities(
            config=self.config if isinstance(self.config, dict) else None,
        )
        self._platform_context = resolve_platform_context(
            self.config if isinstance(self.config, dict) else None,
        )
        self._executed_step_intents: set[tuple[str, str]] = set()
        logger.info("Orchestrator (Quality Pipeline) инициализирован")

    def _step_idempotency_enabled(self) -> bool:
        v = (os.environ.get("TEMIR_STEP_IDEMPOTENCY") or "1").strip().lower()
        return v not in ("0", "false", "no", "off")

    async def _emit_cost(self, delta: float, source: str) -> None:
        if delta and delta > 0:
            run = current_run()
            sid = run.run_id if run else "local"
            await publish_pipeline_event(
                envelope_now(
                    "cost.tick",
                    TraceContext(session_id=sid),
                    {
                        "usd_delta": round(delta, 6),
                        "usd_total": round(self.execution_state.total_cost, 6),
                        "source": source,
                    },
                ),
            )

    async def execute_task(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет задачу в зависимости от роли исполнителя."""
        logger.info(
            f"Выполнение задачи {task.id} (Роль: {task.executor.value}): {task.description}",
        )

        # --- Добавляем контекст для Ревьюера ---
        if task.executor == AIRole.REVIEWER:
            match = re.search(r"file '([^']*)'", task.description)
            if match:
                file_path = match.group(1)
                logger.info(f"Ревьюер запрашивает файл: {file_path}")
                read_result = await asyncio.to_thread(self.tools.read_file, file_path)
                file_content = read_result.get("content") if isinstance(read_result, dict) else read_result
                if file_content and isinstance(read_result, dict) and read_result.get("success"):
                    context["file_to_review"] = file_content
                else:
                    logger.warning(f"Не удалось прочитать файл {file_path} для ревью.")

        # --- Добавляем контекст для CODER при создании тестов ---
        if task.executor == AIRole.CODER and "test" in task.description.lower():
            # Пытаемся найти упоминание исходного файла в описании
            # Ищем паттерны типа "for the FastAPI app in 'src/main.py'" или "for 'src/main.py'"
            match = re.search(r"(?:in|for)\s+['\"]([^'\"]+\.py)['\"]", task.description)
            if match:
                file_path = match.group(1)
                read_result = await asyncio.to_thread(self.tools.read_file, file_path)
                source_content = read_result.get("content") if isinstance(read_result, dict) else read_result
                if source_content and isinstance(read_result, dict) and read_result.get("success"):
                    context["source_code_to_test"] = source_content
                    logger.info(f"CODER получил исходный код для теста: {file_path}")
            else:
                # Fallback: пробуем стандартные пути
                for default_path in ["src/main.py", "main.py"]:
                    read_result = await asyncio.to_thread(self.tools.read_file, default_path)
                    source_content = read_result.get("content") if isinstance(read_result, dict) else read_result
                    if source_content and isinstance(read_result, dict) and read_result.get("success"):
                        context["source_code_to_test"] = source_content
                        logger.info(
                            f"CODER получил исходный код для теста (fallback): {default_path}",
                        )
                        break

        # Передаем обновленный контекст агенту
        # 1) Попытка достать план из кэша
        cached_action = None
        if (
            self.cache
            and self.config.get("cache_enabled", True)
            and not context.get("_bypass_task_cache")
        ):
            try:
                # Сначала ищем точное совпадение
                cached = await asyncio.to_thread(self.cache.find_exact_or_none, task.description, task.executor.value)
                if cached and cached.get("plan_content"):
                    try:
                        cached_action = json.loads(cached["plan_content"])
                        if isinstance(cached_action, dict) and (
                            "action" in cached_action
                            or (
                                isinstance(cached_action.get("actions"), list)
                                and len(cached_action["actions"]) > 0
                            )
                        ):
                            self._cache_hits += 1
                            logger.info(f"Кэш-хит для задачи {task.id}: используем сохранённый план.")
                    except Exception:
                        cached_action = None
                
                # Если точного совпадения нет, ищем похожие
                if not cached_action:
                    self._cache_misses += 1
                    similar_tasks = await asyncio.to_thread(self.cache.find_similar_tasks, task.description)
                    if similar_tasks:
                        logger.info(f"Найдены похожие задачи в кэше для '{task.description[:30]}...':")
                        for similar in similar_tasks:
                            desc = (similar.get("task_description") or "")[:70]
                            logger.info(
                                f"  - (sim: {similar['similarity']:.2f}) {desc}...",
                            )

            except Exception:
                logger.exception("Ошибка доступа к кэшу; продолжаем без кэша")
                cached_action = None

        if cached_action is not None:
            # Для кэшированных действий стоимость равна 0
            cache_action_label = cached_action.get("action")
            if not cache_action_label and isinstance(cached_action.get("actions"), list):
                n = len(cached_action["actions"])
                cache_action_label = f"batch:{n}"
            await publish_pipeline_event(
                "task.cache_hit",
                {
                    "task_id": task.id,
                    "executor": task.executor.value,
                    "action": cache_action_label,
                },
            )
            return {"success": True, "action_json": cached_action, "usage": {"input_tokens": 0, "output_tokens": 0}}

        # 2) Запрос к агенту, если в кэше не нашлось
        await publish_pipeline_event(
            "agent.started",
            {
                "task_id": task.id,
                "role": task.executor.value,
            },
        )
        await publish_pipeline_event(
            "agent.log",
            {
                "task_id": task.id,
                "message": f"LLM/tool plan for {task.executor.value}",
            },
        )
        agent_desc = task.description
        repair_extra = (context or {}).get("_contract_repair")
        if repair_extra:
            agent_desc = f"{task.description}\n\nCONTRACT_REPAIR:\n{repair_extra}"
        agent_response = await self.agent.execute_task(
            agent_desc,
            task.executor,
            context,
        )
        if not agent_response.get("success"):
            await publish_pipeline_event(
                "agent.finished",
                {
                    "task_id": task.id,
                    "role": task.executor.value,
                    "success": False,
                    "error": agent_response.get("error"),
                },
            )
            return agent_response
        await publish_pipeline_event(
            "agent.finished",
            {
                "task_id": task.id,
                "role": task.executor.value,
                "success": True,
            },
        )

        action_for_cache = agent_response.get("action_json")
        if (
            action_for_cache
            and self.cache
            and self.config.get("cache_enabled", True)
            and not context.get("_defer_plan_cache")
        ):
            try:
                await asyncio.to_thread(
                    self.cache.save_plan,
                    task.description,
                    task.executor.value,
                    json.dumps(action_for_cache),
                    True,
                )
            except Exception:
                logger.exception("Не удалось сохранить план в кэш")

        return agent_response

    async def execute_full_pipeline(
        self,
        user_request: str,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.info("Запуск полного конвейера разработки...")
        self.execution_state = ExecutionState(start_time=datetime.now().isoformat())
        self._executed_step_intents.clear()
        active_run_id = attach_pipeline_run()
        try:
            effective_output_dir = output_dir or self.config["output_dir"]
            self.snapshot_manager = SnapshotManager(project_dir=effective_output_dir)
            await publish_pipeline_event(
                "pipeline.started",
                {
                    "user_request": user_request,
                    "output_dir": effective_output_dir,
                },
            )

            # Шаг 1: SYSTEM_ARCHITECT генерирует план выполнения
            architect_task = Task(
                id="architect_plan_generation",
                description=f"Generate a detailed execution plan for the user request: '{user_request}'",
                executor=AIRole.SYSTEM_ARCHITECT,
                dependencies=[],
            )
            logger.info(
                f"SYSTEM_ARCHITECT: Generating execution plan for '{user_request}'...",
            )
            
            # Если точного совпадения нет, ищем похожие, и передаем их в контекст
            similar_tasks_for_architect = []
            if self.cache and self.config.get("cache_enabled", True):
                similar_tasks_for_architect = await asyncio.to_thread(self.cache.find_similar_tasks, architect_task.description)
                if similar_tasks_for_architect:
                    logger.info(f"Найдены похожие задачи в кэше для архитектора: '{architect_task.description[:30]}...'")
                    for similar in similar_tasks_for_architect:
                        desc = (similar.get("task_description") or "")[:70]
                        logger.info(
                            f"  - (sim: {similar['similarity']:.2f}) {desc}...",
                        )

            await publish_pipeline_event(
                envelope_now(
                    "task.started",
                    TraceContext(session_id=active_run_id, task_id=architect_task.id),
                    {
                        "task_id": architect_task.id,
                        "executor": architect_task.executor.value,
                        "description_preview": architect_task.description[:200],
                    },
                ),
            )
            architect_response = await self.execute_task(
                architect_task,
                context={"user_request": user_request, "similar_tasks_in_cache": similar_tasks_for_architect},
            )

            if not architect_response.get("success"):
                await publish_pipeline_event(
                    envelope_now(
                        "task.failed",
                        TraceContext(session_id=active_run_id, task_id=architect_task.id),
                        {
                            "task_id": architect_task.id,
                            "executor": architect_task.executor.value,
                            "error": architect_response.get("error"),
                        },
                    ),
                )
                raise RuntimeError(
                    f"SYSTEM_ARCHITECT failed to generate an execution plan: {architect_response.get('error')}",
                )
            
            # Подсчет стоимости планирования
            if architect_response.get("usage"):
                usage = architect_response["usage"]
                arch_model = architect_response.get("billing_model", "gemini-2.5-pro")
                cost = self.cost_calculator.calculate_cost(
                    arch_model,
                    usage["input_tokens"],
                    usage["output_tokens"],
                )
                self.execution_state.total_cost += cost
                await self._emit_cost(cost, "architect")

            await publish_pipeline_event(
                envelope_now(
                    "task.completed",
                    TraceContext(session_id=active_run_id, task_id=architect_task.id),
                    {
                        "task_id": architect_task.id,
                        "executor": architect_task.executor.value,
                    },
                ),
            )

            raw_execution_plan = architect_response.get("action_json", {}).get("execution_plan")
            if not isinstance(raw_execution_plan, list):
                raise ValueError(
                    "SYSTEM_ARCHITECT returned an invalid execution_plan format (expected a list).",
                )

            # Валидация и преобразование сырого плана в объекты Task
            execution_plan = []
            for i, item in enumerate(raw_execution_plan):
                try:
                    execution_plan.append(Task(**item))
                except Exception as e:  # Ловит Pydantic's ValidationError и другие ошибки
                    logger.warning(
                        f"Skipping invalid task #{i} in execution plan due to validation error: {e}. Task data: {item}",
                    )

            if not execution_plan:
                raise ValueError(
                    "SYSTEM_ARCHITECT generated a plan, but all tasks were invalid or the plan was empty.",
                )

            logger.info(
                f"SYSTEM_ARCHITECT generated {len(execution_plan)} valid tasks.",
            )
            for t in execution_plan:
                await publish_pipeline_event(
                    "task.created",
                    {
                        "task_id": t.id,
                        "executor": t.executor.value,
                        "dependencies": t.dependencies,
                    },
                )
            await publish_pipeline_event(
                "pipeline.plan_ready",
                {
                    "task_count": len(execution_plan),
                    "task_ids": [t.id for t in execution_plan],
                },
            )
            await publish_pipeline_event(
                "decision.strategy.selected",
                {
                    "task_id": architect_task.id,
                    "strategy": "parallel_dag",
                    "reason": "execution_plan_ready",
                    "task_ids": [t.id for t in execution_plan],
                },
            )

            self.execution_state.completed_tasks = []
            self.execution_state.failed_tasks = []
            per_task_results = []
            
            task_futures = {}
            task_events = {task.id: asyncio.Event() for task in execution_plan}

            async def run_task(task):
                task_start_dt = datetime.now()
                
                # Ожидаем завершения зависимостей
                if task.dependencies:
                    try:
                        await asyncio.gather(*(task_events[dep_id].wait() for dep_id in task.dependencies))
                    except KeyError as e:
                        logger.error(f"Task '{task.id}' has an invalid dependency: {e}")
                        await publish_pipeline_event(
                            "task.skipped",
                            {"task_id": task.id, "reason": f"invalid_dependency {e}"},
                        )
                        per_task_results.append({
                            "id": task.id, "success": False, "error": f"Invalid dependency {e}",
                            "duration_seconds": 0.0, "skipped": True
                        })
                        task_events[task.id].set()
                        return

                if any(d in self.execution_state.failed_tasks for d in task.dependencies):
                    logger.warning(f"Skipping task '{task.id}' because a dependency failed.")
                    await publish_pipeline_event(
                        "task.skipped",
                        {"task_id": task.id, "reason": "dependency_failed"},
                    )
                    per_task_results.append({
                        "id": task.id, "success": False, "error": "Dependency failed",
                        "duration_seconds": 0.0, "skipped": True
                    })
                    task_events[task.id].set()
                    return

                # --- Snapshot and Rollback Logic ---
                snapshot_created = False
                modifying_roles = [AIRole.BACKEND_CODER, AIRole.CODER, AIRole.REVIEWER]
                if self.snapshot_manager and task.executor in modifying_roles:
                    snapshot_created = self.snapshot_manager.create_snapshot(task.id)

                try:
                    task_cost = 0  # Инициализируем в начале, чтобы была доступна в except блоке
                    await publish_pipeline_event(
                        "task.started",
                        {
                            "task_id": task.id,
                            "executor": task.executor.value,
                            "description_preview": task.description[:200],
                        },
                    )
                    context = {"output_dir": effective_output_dir}
                    _defer_cache_roles = frozenset(
                        {AIRole.BACKEND_CODER, AIRole.CODER, AIRole.TESTER},
                    )
                    if task.executor in _defer_cache_roles:
                        context["_defer_plan_cache"] = True

                    _repair_max = int(
                        (os.environ.get("TEMIR_PREFLIGHT_REPAIR_ATTEMPTS") or "0").strip() or "0",
                    )
                    last_contract_repair: str | None = None
                    agent_response: Dict[str, Any] | None = None
                    action_json: Any = None
                    steps: list[dict[str, Any]] = []
                    plan_v3: Any = None

                    for repair_attempt in range(_repair_max + 1):
                        if last_contract_repair:
                            context["_contract_repair"] = last_contract_repair
                            context["_bypass_task_cache"] = True
                        else:
                            context.pop("_contract_repair", None)
                            context.pop("_bypass_task_cache", None)
                        agent_response = await self.execute_task(task, context)

                        if not agent_response.get("success"):
                            raise Exception(agent_response.get('error', 'Unknown agent error'))
                        if agent_response.get("usage"):
                            usage = agent_response["usage"]
                            model_name = agent_response.get("billing_model")
                            if not model_name:
                                model_name = (
                                    "mercury-2"
                                    if task.executor == AIRole.BACKEND_CODER
                                    else "gemini-2.5-pro"
                                )
                            add_cost = self.cost_calculator.calculate_cost(
                                model_name,
                                usage["input_tokens"],
                                usage["output_tokens"],
                            )
                            task_cost += add_cost
                            self.execution_state.total_cost += add_cost
                            await self._emit_cost(add_cost, f"task:{task.id}")

                        action_json = agent_response.get("action_json")
                        if not action_json:
                            break
                        if not isinstance(action_json, dict):
                            raise Exception(
                                f"Invalid action_json (must be dict): {action_json!r}",
                            )
                        try:
                            plan_v3 = compile_llm_json_to_execution_plan_v3(
                                action_json,
                                task_id=task.id,
                                registry=self._tool_registry,
                                platform=self._platform_context,
                            )
                            steps = plan_to_executor_dicts(plan_v3)
                            await publish_pipeline_event(
                                "tool.ir.normalized",
                                {
                                    "task_id": task.id,
                                    "step_count": len(steps),
                                    "source": plan_v3.steps[0].meta.source.value,
                                    "execution_mode": plan_v3.execution_mode.value,
                                    "ir_generation": plan_v3.ir_generation,
                                    **platform_event_fields(self._platform_context),
                                },
                            )
                            if len(steps) > 1:
                                await publish_pipeline_event(
                                    "tool.ir.batch_flattened",
                                    {
                                        "task_id": task.id,
                                        "step_count": len(steps),
                                    },
                                )
                        except IRV3ContractError as err:
                            last_contract_repair = f"IR_V3 ({err.code}): {err}"
                            await publish_pipeline_event(
                                "tool.ir.rejected",
                                {
                                    "task_id": task.id,
                                    "code": err.code,
                                    "message": str(err)[:4000],
                                },
                            )
                            if err.code == "schema":
                                await publish_pipeline_event(
                                    "tool.schema.failed",
                                    {
                                        "task_id": task.id,
                                        "error_summary": str(err)[:4000],
                                    },
                                )
                            if not ir_contract_error_retryable(err.code):
                                raise Exception(last_contract_repair) from err
                            if repair_attempt >= _repair_max:
                                raise Exception(last_contract_repair) from err
                            logger.warning(
                                "IR v3 contract failed (%s/%s): %s",
                                repair_attempt + 1,
                                _repair_max,
                                err,
                            )
                            continue
                        try:
                            preflight_tool_steps(
                                steps,
                                project_root=Path(effective_output_dir).resolve(),
                                registry=self._tool_registry,
                                platform=self._platform_context,
                            )
                        except ActionPreflightViolation as err:
                            last_contract_repair = preflight_repair_context_message(err)
                            if not preflight_violation_retryable(err.code):
                                await publish_pipeline_event(
                                    "tool.preflight.failed",
                                    {
                                        "task_id": task.id,
                                        "code": err.code,
                                        "message": str(err),
                                        "repair_hint": err.repair_hint or "",
                                    },
                                )
                                raise Exception(str(err)) from err
                            if repair_attempt >= _repair_max:
                                await publish_pipeline_event(
                                    "tool.preflight.failed",
                                    {
                                        "task_id": task.id,
                                        "code": err.code,
                                        "message": str(err),
                                        "repair_hint": err.repair_hint or "",
                                    },
                                )
                                raise Exception(str(err)) from err
                            logger.warning(
                                "Preflight отклонил план (попытка исправления %s/%s): %s",
                                repair_attempt + 1,
                                _repair_max,
                                err,
                            )
                            continue
                        try:
                            authorize_plan_steps(
                                plan_v3.steps,
                                self._allowed_capabilities,
                            )
                        except CapabilityDeniedError as cerr:
                            await publish_pipeline_event(
                                "audit.capability.denied",
                                {
                                    "task_id": task.id,
                                    "action": cerr.action,
                                    "code": cerr.code,
                                    "missing": sorted(cerr.missing),
                                    "message": str(cerr)[:4000],
                                },
                            )
                            raise Exception(str(cerr)) from cerr
                        last_contract_repair = None
                        break

                    context.pop("_contract_repair", None)
                    context.pop("_bypass_task_cache", None)

                    if not action_json:
                         # Обработка для ревьюера, который может вернуть текст
                        if task.executor == AIRole.REVIEWER and agent_response and agent_response.get("output_text"):
                             result = {"success": True, "output": agent_response.get("output_text")}
                        else:
                            raise Exception("No action_json in agent response")
                    else:
                        if not isinstance(action_json, dict):
                            raise Exception(
                                f"Invalid action_json (must be dict): {action_json!r}",
                            )
                        if not steps or plan_v3 is None:
                            raise Exception(
                                "Internal error: expected validated tool steps after contract loop",
                            )
                        if len(steps) > 1:
                            logger.info(
                                "IR: план из %d шагов (%s) для задачи %s",
                                len(steps),
                                plan_v3.execution_mode.value,
                                task.id,
                            )
                        levels = execution_levels_for_plan(plan_v3)
                        result: Any = True
                        cache_saved_after_first_success = False
                        global_idx = 0
                        root = Path(effective_output_dir).resolve()
                        idem = self._step_idempotency_enabled()

                        async def run_single_step(
                            sv3: Any,
                            step_idx: int,
                            level_i: int,
                        ) -> tuple[bool, Any, dict[str, Any]]:
                            nonlocal result, cache_saved_after_first_success
                            action_name = sv3.action
                            action_args = dict(sv3.args)
                            caps = sorted(capabilities_required_for_action(action_name))
                            _, intent_sha256 = compute_step_intent_sha256(
                                task_id=task.id,
                                step_id=sv3.id,
                                step_seq=step_idx,
                                action=action_name,
                                args=action_args,
                                level_index=level_i,
                                capabilities=caps,
                            )
                            rec: dict[str, Any] = {
                                "step_id": sv3.id,
                                "intent_sha256": intent_sha256,
                                "completed": False,
                                "skipped_idempotent": False,
                            }
                            decision = can_execute_tool_step(
                                step_dict={"action": action_name, "args": action_args},
                                task_id=task.id,
                                project_root=root,
                                registry=self._tool_registry,
                                platform=self._platform_context,
                                allowed_capabilities=self._allowed_capabilities,
                                executed_intents=self._executed_step_intents,
                                intent_sha256=intent_sha256,
                                idempotency_enabled=idem,
                            )
                            if not decision.allowed:
                                raise Exception(
                                    f"Execution gate denied: {decision.reason}",
                                )
                            step_label = f"{sv3.id} ({step_idx + 1}/{len(plan_v3.steps)})"
                            logger.info(
                                "Агент шаг %s: действие '%s', аргументы: %s",
                                step_label,
                                action_name,
                                action_args,
                            )
                            if decision.skipped_idempotent:
                                rec["completed"] = True
                                rec["skipped_idempotent"] = True
                                await publish_pipeline_event(
                                    "audit.step.record",
                                    {
                                        "task_id": task.id,
                                        "step_id": sv3.id,
                                        "step_seq": step_idx,
                                        "action": action_name,
                                        "level_index": level_i,
                                        "intent_sha256": intent_sha256,
                                        "capabilities": caps,
                                        "success": True,
                                        "idempotent_skip": True,
                                    },
                                )
                                return True, {"success": True, "idempotent_skip": True}, rec
                            patch_like = action_name in (
                                "write_file",
                                "append_file",
                                "smart_patch",
                            )
                            if patch_like:
                                await publish_pipeline_event(
                                    "patch.proposed",
                                    {
                                        "task_id": task.id,
                                        "summary": summarize_tool_action(action_name, action_args),
                                        "batch_index": step_idx,
                                        "batch_total": len(plan_v3.steps),
                                        "step_id": sv3.id,
                                    },
                                )
                            tool_method = self._tool_registry.get(action_name)
                            if tool_method is None:
                                raise Exception(f"Инструмент '{action_name}' не найден в реестре.")
                            await publish_pipeline_event(
                                "tool.execution.started",
                                {
                                    "task_id": task.id,
                                    "tool": action_name,
                                    "arg_keys": list(action_args.keys()),
                                    "batch_index": step_idx,
                                    "batch_total": len(plan_v3.steps),
                                    "step_id": sv3.id,
                                },
                            )
                            if action_name == "run_tests":
                                await publish_pipeline_event(
                                    "evaluation.test.run",
                                    {
                                        "task_id": task.id,
                                        "path_or_command": str(action_args.get("path", "tests")),
                                    },
                                )
                            step_result = await asyncio.to_thread(tool_method, **action_args)
                            result = step_result
                            if patch_like:
                                ok = step_result if isinstance(step_result, bool) else (
                                    step_result.get("success", True)
                                    if isinstance(step_result, dict)
                                    else True
                                )
                                if ok:
                                    await publish_pipeline_event(
                                        "patch.applied",
                                        {
                                            "task_id": task.id,
                                            "action": action_name,
                                            "batch_index": step_idx,
                                            "step_id": sv3.id,
                                        },
                                    )
                                else:
                                    await publish_pipeline_event(
                                        "patch.failed",
                                        {
                                            "task_id": task.id,
                                            "action": action_name,
                                            "detail": step_result
                                            if isinstance(step_result, dict)
                                            else str(step_result),
                                            "batch_index": step_idx,
                                            "step_id": sv3.id,
                                        },
                                    )
                            step_ok = (
                                step_result
                                if isinstance(step_result, bool)
                                else (
                                    step_result.get("success", True)
                                    if isinstance(step_result, dict)
                                    else True
                                )
                            )
                            rec["completed"] = bool(step_ok)
                            if (
                                step_ok
                                and not cache_saved_after_first_success
                                and context.get("_defer_plan_cache")
                                and self.cache
                                and self.config.get("cache_enabled", True)
                            ):
                                try:
                                    await asyncio.to_thread(
                                        self.cache.save_plan,
                                        task.description,
                                        task.executor.value,
                                        json.dumps(action_json),
                                        True,
                                    )
                                    cache_saved_after_first_success = True
                                except Exception:
                                    logger.exception(
                                        "Не удалось сохранить план в кэш после первого успешного шага",
                                    )
                            if step_ok:
                                register_successful_intent(
                                    self._executed_step_intents,
                                    task_id=task.id,
                                    intent_sha256=intent_sha256,
                                )
                            await publish_pipeline_event(
                                "audit.step.record",
                                {
                                    "task_id": task.id,
                                    "step_id": sv3.id,
                                    "step_seq": step_idx,
                                    "action": action_name,
                                    "level_index": level_i,
                                    "intent_sha256": intent_sha256,
                                    "capabilities": caps,
                                    "success": step_ok,
                                },
                            )
                            return step_ok, step_result, rec

                        for level_i, level in enumerate(levels):
                            parallel_eligible = level_allows_parallel_gather(level)
                            await publish_pipeline_event(
                                "execution.level.started",
                                {
                                    "task_id": task.id,
                                    "level_index": level_i,
                                    "step_ids": [s.id for s in level],
                                    "mode": plan_v3.execution_mode.value,
                                    "parallel_eligible": parallel_eligible,
                                    **platform_event_fields(self._platform_context),
                                },
                            )
                            indices = list(range(global_idx, global_idx + len(level)))
                            level_records: list[dict[str, Any]] = []
                            if parallel_eligible:
                                outcomes = await asyncio.gather(
                                    *[
                                        run_single_step(sv, indices[si], level_i)
                                        for si, sv in enumerate(level)
                                    ],
                                )
                                level_records = [t[2] for t in outcomes]
                                for (ok, _sr, _rec), sv in zip(outcomes, level):
                                    if not ok:
                                        raise Exception(
                                            f"Tool step {sv.id} ({sv.action}) failed",
                                        )
                                global_idx += len(level)
                            else:
                                for si, sv in enumerate(level):
                                    ok, _sr, step_rec = await run_single_step(
                                        sv,
                                        indices[si],
                                        level_i,
                                    )
                                    level_records.append(step_rec)
                                    if not ok:
                                        raise Exception(
                                            f"Tool step {sv.id} ({sv.action}) failed",
                                        )
                                    global_idx += 1
                            try:
                                validate_level_completion(
                                    level,
                                    level_records,
                                    idempotency_enabled=idem,
                                )
                            except LevelCompletionError as lerr:
                                raise Exception(
                                    f"Level {level_i} validation failed: {lerr}",
                                ) from lerr

                    task_end_dt = datetime.now()
                    task_duration = (task_end_dt - task_start_dt).total_seconds()
                    
                    was_successful = result if isinstance(result, bool) else result.get("success", True)

                    if was_successful:
                        self.execution_state.completed_tasks.append(task.id)
                        await publish_pipeline_event(
                            "task.completed",
                            {
                                "task_id": task.id,
                                "executor": task.executor.value,
                                "duration_seconds": round(task_duration, 3),
                            },
                        )
                        per_task_results.append(
                            {
                                "id": task.id, "executor": task.executor.value, "description": task.description,
                                "success": True, "stdout": result.get("stdout") if isinstance(result, dict) else None, 
                                "stderr": result.get("stderr") if isinstance(result, dict) else None,
                                "output": result.get("output") if isinstance(result, dict) else str(result), 
                                "duration_seconds": task_duration, "cost": task_cost,
                            }
                        )
                    else:
                        # Улучшенная обработка ошибок - извлекаем информацию из stderr или error
                        if isinstance(result, dict):
                            error_msg = result.get('error') or result.get('stderr') or result.get('warning')
                            if error_msg:
                                raise Exception(str(error_msg))
                            else:
                                # Если нет явной ошибки, но success=False, формируем сообщение
                                stderr = result.get('stderr', '')
                                stdout = result.get('stdout', '')
                                if stderr:
                                    raise Exception(f"Tool execution failed: {stderr}")
                                elif stdout:
                                    raise Exception(f"Tool execution failed (exit code: {result.get('exit_code', 'unknown')})")
                                else:
                                    raise Exception("Tool execution failed: Unknown error")
                        else:
                            raise Exception(f"Tool execution failed: {str(result)}")


                except Exception as task_error:
                    # --- Task Failed ---
                    self.execution_state.failed_tasks.append(task.id)
                    error_message = str(task_error)
                    logger.error(f"Task {task.id} failed: {error_message}")
                    await publish_pipeline_event(
                        "task.failed",
                        {
                            "task_id": task.id,
                            "executor": task.executor.value,
                            "error": error_message,
                        },
                    )
                    await publish_pipeline_event(
                        "reflection.loop.triggered",
                        {
                            "task_id": task.id,
                            "phase": "supervisor",
                        },
                    )
                    await publish_pipeline_event(
                        "decision.execution.fallback",
                        {
                            "task_id": task.id,
                            "reason": error_message[:2000],
                        },
                    )

                    # --- Invoke SUPERVISOR for Decision ---
                    supervisor_response = await self.agent.execute_task(
                        task_description=f"Task '{task.id}' failed.",
                        role=AIRole.SUPERVISOR,
                        context={"failed_task": task.description, "error_message": error_message},
                    )
                    
                    supervisor_cost = 0
                    if supervisor_response.get("usage"):
                        usage = supervisor_response["usage"]
                        sup_model = supervisor_response.get(
                            "billing_model",
                            "gemini-2.5-pro",
                        )
                        supervisor_cost = self.cost_calculator.calculate_cost(
                            sup_model,
                            usage["input_tokens"],
                            usage["output_tokens"],
                        )
                        self.execution_state.total_cost += supervisor_cost
                        await self._emit_cost(supervisor_cost, "supervisor")
                    
                    decision = supervisor_response.get("action_json", {}).get("decision")
                    reason = supervisor_response.get("action_json", {}).get("reason", "No reason provided by supervisor.")
                    alt = supervisor_response.get("action_json", {}).get("alternatives")
                    await publish_pipeline_event(
                        "decision.selected",
                        {
                            "task_id": task.id,
                            "decision": decision,
                            "reason": reason,
                        },
                    )
                    if alt is not None:
                        await publish_pipeline_event(
                            "decision.alternatives",
                            {"task_id": task.id, "alternatives": alt},
                        )

                    if decision == "rollback" and snapshot_created:
                        logger.warning(f"SUPERVISOR decided to ROLLBACK for task {task.id}. Reason: {reason}")
                        self.snapshot_manager.restore_snapshot(task.id)
                        per_task_results.append(
                            {
                                "id": task.id, "executor": task.executor.value, "description": task.description,
                                "success": False, "error": error_message, "duration_seconds": (datetime.now() - task_start_dt).total_seconds(),
                                "cost": task_cost + supervisor_cost, "supervisor_decision": {"decision": decision, "reason": reason}
                            }
                        )
                        if not self.config.get("continue_on_failure"):
                            raise Exception(f"Task {task.id} failed and supervisor rolled back. Continue on failure is False.")
                    elif decision == "proceed":
                        logger.warning(f"SUPERVISOR decided to PROCEED despite task {task.id} failure. Reason: {reason}")
                        per_task_results.append(
                            {
                                "id": task.id, "executor": task.executor.value, "description": task.description,
                                "success": False, "error": error_message, "duration_seconds": (datetime.now() - task_start_dt).total_seconds(),
                                "cost": task_cost + supervisor_cost, "supervisor_decision": {"decision": decision, "reason": reason}
                            }
                        )
                        if not self.config.get("continue_on_failure"):
                            raise Exception(f"Task {task.id} failed and supervisor proceeded. Continue on failure is False.")
                    else:
                        logger.error(f"SUPERVISOR made an invalid decision ('{decision}') or did not provide one for task {task.id}. Defaulting to rollback if snapshot exists.")
                        if snapshot_created:
                            self.snapshot_manager.restore_snapshot(task.id)
                        per_task_results.append(
                            {
                                "id": task.id, "executor": task.executor.value, "description": task.description,
                                "success": False, "error": error_message, "duration_seconds": (datetime.now() - task_start_dt).total_seconds(),
                                "cost": task_cost + supervisor_cost, "supervisor_decision": {"decision": decision, "reason": reason, "defaulted_to_rollback": True}
                            }
                        )
                        if not self.config.get("continue_on_failure"):
                            raise Exception(f"Task {task.id} failed and supervisor decision was invalid. Defaulted to rollback.")
                finally:
                    # --- Cleanup Snapshot ---
                    if snapshot_created:
                        self.snapshot_manager.delete_snapshot(task.id)
                    
                    task_events[task.id].set()


            try:
                await asyncio.gather(*(run_task(task) for task in execution_plan))
            except Exception as e:
                logger.error(f"Pipeline execution failed: {e}")


            skipped_tasks = [t for t in per_task_results if t.get("skipped")]
            success = not self.execution_state.failed_tasks and not skipped_tasks
            self.execution_state.end_time = datetime.now().isoformat()
            total_tasks = len([t for t in per_task_results if not t.get("skipped")])
            completed = len(self.execution_state.completed_tasks)
            failed = len(self.execution_state.failed_tasks)
            # Оценка длительности
            try:
                start_dt = (
                    datetime.fromisoformat(self.execution_state.start_time)
                    if self.execution_state.start_time
                    else None
                )
                end_dt = (
                    datetime.fromisoformat(self.execution_state.end_time)
                    if self.execution_state.end_time
                    else None
                )
                duration_seconds = (
                    (end_dt - start_dt).total_seconds() if start_dt and end_dt else None
                )
            except Exception:
                duration_seconds = None
            
            if self.snapshot_manager:
                self.snapshot_manager.cleanup_snapshots()

            logger.info("Полный конвейер выполнен.")
            generated_files: list[str] = []
            if self.config.get("collect_artifacts"):
                try:
                    base = Path(effective_output_dir)
                    if base.exists():
                        include_patterns = self.config.get("artifacts_include") or []
                        exclude_patterns = self.config.get("artifacts_exclude") or []
                        # системные игноры
                        default_exclude = [
                            "**/__pycache__/**",
                            "**/.pytest_cache/**",
                            "**/.venv/**",
                            "**/.git/**",
                            "**/.snapshots/**", # Игнорируем папку со снэпшотами
                        ]
                        from fnmatch import fnmatch

                        for p in base.rglob("*"):
                            if not p.is_file():
                                continue
                            rel = str(p.relative_to(base))
                            # исключения
                            excluded = any(
                                fnmatch(rel, pat.replace("**/", "*"))
                                or fnmatch(rel, pat)
                                for pat in (exclude_patterns + default_exclude)
                            )
                            if excluded:
                                continue
                            # включения
                            if include_patterns:
                                if any(fnmatch(rel, pat) for pat in include_patterns):
                                    generated_files.append(rel)
                            else:
                                generated_files.append(rel)
                except Exception as scan_err:
                    logger.warning(f"Не удалось собрать артефакты: {scan_err}")
            await publish_pipeline_event(
                "pipeline.completed",
                {
                    "success": success,
                    "total_cost_usd": round(self.execution_state.total_cost, 6),
                    "completed": completed,
                    "failed": failed,
                },
            )
            return {
                "success": success,
                "run_id": active_run_id,
                "summary": {
                    "total_tasks": total_tasks,
                    "completed": completed,
                    "failed": failed,
                    "start_time": self.execution_state.start_time,
                    "end_time": self.execution_state.end_time,
                    "duration_seconds": duration_seconds,
                    "cache_hits": self._cache_hits,
                    "cache_misses": self._cache_misses,
                    "total_cost_usd": self.execution_state.total_cost,
                    "run_id": active_run_id,
                },
                "tasks": per_task_results,
                "completed_tasks": self.execution_state.completed_tasks,
                "failed_tasks": self.execution_state.failed_tasks,
                "generated_files": generated_files,
            }
        except (ValueError, RuntimeError) as e:
            logger.error(f"Ошибка подготовки конвейера: {e}", exc_info=True)
            await publish_pipeline_event(
                "pipeline.failed",
                {"phase": "setup", "error": str(e)},
            )
            return {"success": False, "error": str(e), "run_id": active_run_id}
        except Exception as e:
            logger.error(f"Критическая ошибка в конвейере: {e}", exc_info=True)
            await publish_pipeline_event(
                "pipeline.failed",
                {"phase": "execution", "error": str(e)},
            )
            if self.snapshot_manager:
                self.snapshot_manager.cleanup_snapshots()
            return {"success": False, "error": str(e), "run_id": active_run_id}
        finally:
            detach_pipeline_run()
