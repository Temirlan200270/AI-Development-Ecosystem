"""
Temir Execution Guard v1: preflight + env context + repair hints.
Используется как «обёртка безопасного запуска» перед пайплайном (совместно с execution_contract).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

from temir.env_bootstrap import dotenv_candidate_paths
from temir.execution_contract import (
    ExecutionContractResult,
    RuntimeAssumptions,
    collect_runtime_assumptions,
    enforce_execution_contract,
)

# (substring in issue, repair lines)
_REPAIR_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "GEMINI_API_KEY",
        (
            "Создайте файл .env в корне проекта или в текущем каталоге и добавьте: GEMINI_API_KEY=...",
            "PowerShell (только сессия): $env:GEMINI_API_KEY = \"ваш_ключ\"",
            "Убедитесь, что ключ не перекрыт пустой строкой в системных переменных (override=False у dotenv).",
        ),
    ),
    (
        "Виртуальное окружение не активно",
        (
            "Создайте и активируйте venv из корня репозитория:",
            "  python -m venv .venv",
            "  .\\.venv\\Scripts\\Activate.ps1   # PowerShell",
            "  .\\.venv\\Scripts\\activate.bat   # cmd",
            "Установка в это окружение: python -m pip install -e \".[dev]\"",
            "Или снимите обязательность venv: не задавайте TEMIR_REQUIRE_VENV=1.",
        ),
    ),
    (
        "INCEPTION_API_KEY обязателен",
        (
            "В .env добавьте INCEPTION_API_KEY=... (Inception Labs / mercury).",
            "Или отключите строгий режим: не задавайте TEMIR_REQUIRE_INCEPTION=1.",
        ),
    ),
    (
        "Журнал событий недоступен",
        (
            "Проверьте права на каталог и переменную TEMIR_EVENT_JOURNAL_DIR.",
            "По умолчанию журнал: .andromeda/runs относительно текущего cwd.",
        ),
    ),
    (
        "Цепочка моделей Gemini пуста",
        (
            "Задайте GEMINI_MODEL_CHAIN=model1,model2 (без пробелов или с trim).",
            "Или удалите переменную, чтобы использовать цепочку по умолчанию из кода.",
        ),
    ),
    (
        "Недопустимое имя модели",
        (
            "Имена моделей: буквы, цифры, точка, подчёркивание, дефис; первый символ — буква или цифра.",
            "Пример: GEMINI_MODEL_CHAIN=gemini-2.5-flash,gemini-2.5-pro",
        ),
    ),
    (
        "Директория результатов недоступна",
        (
            "Укажите доступный путь: temir run ... -o C:\\path\\to\\output",
            "Проверьте права на запись и что диск не только для чтения.",
        ),
    ),
    (
        "Требуется Python >=",
        (
            "Установите Python 3.8+ и перезапустите shell; проверка: python --version",
            "В IDE выберите интерпретатор из venv проекта.",
        ),
    ),
    (
        "В цепочке Gemini есть пустое имя",
        (
            "Уберите лишние запятые в GEMINI_MODEL_CHAIN и пустые элементы.",
        ),
    ),
)


def collect_repair_hints(issues: Sequence[str]) -> Tuple[str, ...]:
    """Подсказки по известным классам ошибок preflight (дедупликация по тексту)."""
    seen: set[str] = set()
    out: List[str] = []
    for issue in issues:
        for needle, hints in _REPAIR_RULES:
            if needle in issue:
                for h in hints:
                    if h not in seen:
                        seen.add(h)
                        out.append(h)
                break
    return tuple(out)


def gather_env_file_warnings(assumptions: RuntimeAssumptions) -> Tuple[str, ...]:
    """Защита от silent misconfig: нет .env, хотя ключи часто ожидаются из файла."""
    paths = dotenv_candidate_paths()
    any_file = any(p.is_file() for p in paths)
    if any_file:
        return tuple()
    if assumptions.gemini_key_configured and assumptions.inception_key_configured:
        return tuple()
    lines: List[str] = [
        "Файл .env не найден ни в cwd, ни в корне репозитория — ключи должны приходить из окружения ОС.",
    ]
    if paths:
        lines.append(f"Ожидаемые пути: {', '.join(str(p) for p in paths)}")
    return tuple(lines)


_IMPORT_CHECKS: Tuple[Tuple[str, str], ...] = (
    ("typer", "typer[all]>=0.9.0"),
    ("google.generativeai", "google-generativeai>=0.3.0"),
    ("yaml", "pyyaml>=6.0"),
    ("httpx", "httpx>=0.25.0"),
    ("fastapi", "fastapi>=0.100.0"),
)


def check_runtime_imports() -> Tuple[str, ...]:
    """Проверка импорта критичных зависимостей (отдельно от preflight файловой системы)."""
    errors: List[str] = []
    for module, pip_spec in _IMPORT_CHECKS:
        try:
            __import__(module)
        except ImportError as e:
            errors.append(f"Не удалось import {module}: {e}. Установите: pip install \"{pip_spec}\"")
    return tuple(errors)


def run_guard_preflight(
    *,
    dry_run: bool,
    output_dir: Path,
    skip: bool = False,
) -> ExecutionContractResult:
    """Тот же контракт, что у безопасного `temir run` (единая точка правды)."""
    return enforce_execution_contract(dry_run=dry_run, output_dir=output_dir, skip=skip)


def snapshot_kernel_execution_context() -> RuntimeAssumptions:
    """Снимок контекста для логов/диагностики без побочных проверок записи."""
    return collect_runtime_assumptions()


def format_assumptions_lines(a: RuntimeAssumptions) -> Tuple[str, ...]:
    """Человекочитаемый блок для `temir guard`."""
    return (
        f"python: {a.python_executable} ({a.python_version})",
        f"cwd: {a.cwd}",
        f"venv/conda: {a.virtual_env or '(none)'}",
        f"GEMINI_API_KEY: {'задан' if a.gemini_key_configured else 'нет'}",
        f"INCEPTION_API_KEY: {'задан' if a.inception_key_configured else 'нет'}",
        f"journal_base: {a.journal_base}",
        f"gemini_chain: {','.join(a.gemini_model_chain)}",
    )
