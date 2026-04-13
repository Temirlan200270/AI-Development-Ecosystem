"""
Execution Contract Layer: фиксирует допущения среды и preflight до запуска пайплайна.
Цель — fail-fast до инициализации агентов и вызовов LLM.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

_MODEL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-]{0,127}$")


@dataclass(frozen=True)
class RuntimeAssumptions:
    """Снимок среды на момент проверки (иммутабельный)."""

    python_executable: str
    python_version: str
    cwd: Path
    virtual_env: Optional[str]
    in_venv: bool
    gemini_key_configured: bool
    inception_key_configured: bool
    journal_base: Path
    gemini_model_chain: Tuple[str, ...]


class ExecutionContractError(Exception):
    """Нарушение контракта исполнения; сообщения для вывода пользователю."""

    def __init__(self, issues: Tuple[str, ...]) -> None:
        self.issues = issues
        super().__init__("; ".join(issues))


@dataclass(frozen=True)
class ExecutionContractResult:
    assumptions: RuntimeAssumptions
    warnings: Tuple[str, ...]
    skipped: bool


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _skip_contract() -> bool:
    return _truthy_env("TEMIR_SKIP_EXEC_CONTRACT")


def collect_runtime_assumptions() -> RuntimeAssumptions:
    from temir.storage.event_journal import get_journal_base
    from temir.agents.gemini_chain import get_gemini_model_chain

    venv = os.environ.get("VIRTUAL_ENV", "").strip()
    conda = os.environ.get("CONDA_PREFIX", "").strip()
    active_env = venv or conda or None
    chain = tuple(get_gemini_model_chain())
    gemini = bool((os.environ.get("GEMINI_API_KEY") or "").strip())
    inception = bool((os.environ.get("INCEPTION_API_KEY") or "").strip())
    return RuntimeAssumptions(
        python_executable=sys.executable,
        python_version=".".join(str(x) for x in sys.version_info[:3]),
        cwd=Path.cwd().resolve(),
        virtual_env=active_env,
        in_venv=active_env is not None,
        gemini_key_configured=gemini,
        inception_key_configured=inception,
        journal_base=get_journal_base().resolve(),
        gemini_model_chain=chain,
    )


def _validate_python_version(issues: List[str]) -> None:
    if sys.version_info < (3, 8):
        issues.append(
            f"Требуется Python >= 3.8 (текущий {sys.version_info.major}.{sys.version_info.minor}).",
        )


def _validate_venv(assumptions: RuntimeAssumptions, issues: List[str], warnings: List[str]) -> None:
    if assumptions.in_venv:
        return
    if _truthy_env("TEMIR_REQUIRE_VENV"):
        issues.append(
            "Виртуальное окружение не активно (нет VIRTUAL_ENV). "
            "Активируйте venv или отключите строгий режим: снимите TEMIR_REQUIRE_VENV.",
        )
        return
    warnings.append(
        "Рекомендуется активированный venv (VIRTUAL_ENV не задан). "
        "Для обязательной проверки: TEMIR_REQUIRE_VENV=1.",
    )


def _validate_gemini_key(dry_run: bool, assumptions: RuntimeAssumptions, issues: List[str]) -> None:
    if dry_run:
        return
    if not assumptions.gemini_key_configured:
        issues.append(
            "GEMINI_API_KEY не задан или пуст. Задайте ключ в окружении или в .env — LLM не будет вызван.",
        )


def _validate_inception(assumptions: RuntimeAssumptions, issues: List[str], warnings: List[str]) -> None:
    if assumptions.inception_key_configured:
        return
    if _truthy_env("TEMIR_REQUIRE_INCEPTION"):
        issues.append(
            "INCEPTION_API_KEY обязателен (TEMIR_REQUIRE_INCEPTION=1), но не задан.",
        )
        return
    warnings.append(
        "INCEPTION_API_KEY не задан — BackendCoder (mercury) может быть недоступен; "
        "будет полагаться на Gemini fallback.",
    )


def _validate_model_chain(chain: Tuple[str, ...], issues: List[str]) -> None:
    if not chain:
        issues.append("Цепочка моделей Gemini пуста (проверьте GEMINI_MODEL_CHAIN).")
        return
    for m in chain:
        if not m or not m.strip():
            issues.append("В цепочке Gemini есть пустое имя модели.")
            return
        if not _MODEL_ID_RE.match(m.strip()):
            issues.append(
                f"Недопустимое имя модели в цепочке: {m!r} (ожидаются буквы, цифры, ._-).",
            )
            return


def _try_write_probe(dir_path: Path) -> Optional[str]:
    """Создаёт каталог (если нужно) и проверяет возможность записи файла внутри него."""
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"Не удалось создать каталог {dir_path}: {e}"
    probe = dir_path / f".temir_exec_probe_{uuid.uuid4().hex}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as e:
        return f"Нет прав на запись в {dir_path}: {e}"
    return None


def _validate_journal(assumptions: RuntimeAssumptions, issues: List[str]) -> None:
    err = _try_write_probe(assumptions.journal_base)
    if err:
        issues.append(f"Журнал событий недоступен ({assumptions.journal_base}): {err}")


def _validate_output_dir(output_dir: Path, issues: List[str]) -> None:
    resolved = output_dir.resolve()
    err = _try_write_probe(resolved)
    if err:
        issues.append(f"Директория результатов недоступна ({resolved}): {err}")


def enforce_execution_contract(
    *,
    dry_run: bool,
    output_dir: Path,
    skip: bool = False,
) -> ExecutionContractResult:
    """
    Выполняет preflight. Бросает ExecutionContractError при фатальных нарушениях.
    """
    if skip or _skip_contract():
        return ExecutionContractResult(
            assumptions=collect_runtime_assumptions(),
            warnings=(
                "Execution Contract пропущен (TEMIR_SKIP_EXEC_CONTRACT или skip=True).",
            ),
            skipped=True,
        )

    assumptions = collect_runtime_assumptions()
    issues: List[str] = []
    warnings: List[str] = []

    _validate_python_version(issues)
    _validate_venv(assumptions, issues, warnings)
    _validate_output_dir(output_dir, issues)

    if not dry_run:
        _validate_gemini_key(dry_run, assumptions, issues)
        _validate_inception(assumptions, issues, warnings)
        _validate_model_chain(assumptions.gemini_model_chain, issues)
        _validate_journal(assumptions, issues)

    if issues:
        raise ExecutionContractError(tuple(issues))

    return ExecutionContractResult(
        assumptions=assumptions,
        warnings=tuple(warnings),
        skipped=False,
    )
