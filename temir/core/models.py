"""
Pydantic модели для валидации спецификации и задач.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, ConfigDict


class TemirTimeouts(BaseModel):
    task: int = Field(default=300, description="Таймаут на одну задачу, сек")
    tool: int = Field(default=60, description="Таймаут для инструментов, сек")


class TemirConfig(BaseModel):
    output_dir: str = "./output"
    continue_on_failure: bool = False
    cache_enabled: bool = True
    sandbox_enabled: bool = True
    collect_artifacts: bool = False
    artifacts_include: Optional[List[str]] = None
    artifacts_exclude: Optional[List[str]] = None
    timeouts: TemirTimeouts = Field(default_factory=TemirTimeouts)
    log_format: str = Field(default="text", description="text|json")


class AIRole(str, Enum):
    """Роли AI-агентов."""

    PLANNER = "PLANNER"
    CODER = "CODER"
    REVIEWER = "REVIEWER"
    TESTER = "TESTER"
    SYSTEM_ARCHITECT = "SYSTEM_ARCHITECT"
    BACKEND_CODER = "BACKEND_CODER"
    SUPERVISOR = "SUPERVISOR"


class ExecutionPhase(str, Enum):
    """Фазы выполнения."""

    IDLE = "idle"
    INITIALIZATION = "initialization"
    EXECUTION = "execution"
    VALIDATION = "validation"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"


class Task(BaseModel):
    """Модель задачи из execution_plan."""

    id: str = Field(..., description="Уникальный идентификатор задачи")
    description: str = Field(..., description="Описание задачи")
    executor: AIRole = Field(default=AIRole.CODER, description="Исполнитель задачи")
    dependencies: List[str] = Field(
        default_factory=list,
        description="Список зависимостей",
    )
    tools: List[str] = Field(
        default_factory=list,
        description="Список инструментов для задачи",
    )

    @field_validator("id")
    def validate_id(cls, v):
        if not v or not v.strip():
            raise ValueError("ID задачи не может быть пустым")
        return v.strip()

    @field_validator("description")
    def validate_description(cls, v):
        if not v or not v.strip():
            raise ValueError("Описание задачи не может быть пустым")
        return v.strip()


class Project(BaseModel):
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    primary_dependency: Optional[str] = None


class Specification(BaseModel):
    """Спецификация в формате, совместимом с spec.yaml.

    Основное поле — `project: Project`. Остальные секции считаются опциональными
    и допускают произвольную структуру (через extra fields).
    """

    model_config = ConfigDict(extra="allow")

    project: Project
    execution_plan: Optional[List[Task]] = Field(default_factory=list)

    @field_validator("execution_plan")
    def validate_execution_plan(cls, v):
        # Если план пуст, допускаем это (иногда план генерируется позже)
        if not v:
            return v

        task_ids = [task.id for task in v]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("ID задач должны быть уникальными")

        for task in v:
            for dep in task.dependencies:
                if dep not in task_ids:
                    raise ValueError(f"Зависимость {dep} не найдена в списке задач")

        return v


class ExecutionState(BaseModel):
    """Модель состояния выполнения."""

    model_config = ConfigDict(use_enum_values=True)

    current_phase: ExecutionPhase = Field(
        default=ExecutionPhase.IDLE,
        description="Текущая фаза",
    )
    current_task: Optional[str] = Field(default=None, description="Текущая задача")
    start_time: Optional[str] = Field(default=None, description="Время начала")
    end_time: Optional[str] = Field(default=None, description="Время окончания")
    completed_tasks: List[str] = Field(
        default_factory=list,
        description="Выполненные задачи",
    )
    failed_tasks: List[str] = Field(
        default_factory=list,
        description="Неудачные задачи",
    )
    total_tasks: int = Field(default=0, description="Общее количество задач")
    successful_tasks: int = Field(default=0, description="Количество успешных задач")
    failed_tasks_count: int = Field(default=0, description="Количество неудачных задач")
    error: Optional[str] = Field(default=None, description="Ошибка выполнения")
    total_cost: float = Field(default=0.0, description="Общая стоимость выполнения в USD")

