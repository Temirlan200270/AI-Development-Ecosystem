"""Temir CLI - Typer-based entrypoint with Dependency Injection."""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any

import typer
import yaml
from pydantic import BaseModel

from temir.agents.universal_agent import UniversalAgent
from temir.agents.backend_coder_agent import BackendCoderAgent
from temir.agents.gemini_enhancer_agent import GeminiEnhancerAgent
from temir.agents.system_architect_agent import SystemArchitectAgent
from temir.agents.tester_agent import TesterAgent
from temir.agents.supervisor_agent import SupervisorAgent
from temir.core.models import TemirConfig
from temir.core.orchestrator import Orchestrator
from temir.memory.cache_manager import CacheManager
from temir.core.rate_limiter import TokenBucket
from temir.sandbox.docker_manager import DockerManager
from temir.sandbox.local_sandbox import LocalUnsafeSandbox
from temir.env_bootstrap import load_dotenv_if_available
from temir.execution_contract import ExecutionContractError
from temir.execution_guard import (
    check_runtime_imports,
    collect_repair_hints,
    format_assumptions_lines,
    gather_env_file_warnings,
    run_guard_preflight,
)
from temir.tools.agent_tools import AgentTools
from temir.smoke_v1 import (
    build_report,
    load_events_jsonl,
    read_smoke_prompt,
    smoke_prompt_file,
)
from temir.journal_cli import journal_app
from temir.storage.event_journal import get_journal_base, sanitize_run_id
from temir.storage.run_store import list_run_ids, load_run_events

load_dotenv_if_available()


class CustomJSONEncoder(json.JSONEncoder):
    """Специальный кодировщик JSON для Pydantic моделей, Enum и datetime."""

    def default(self, obj):
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json")
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        return json.JSONEncoder.default(self, obj)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# Создаем главный объект Typer
app = typer.Typer(
    help="Temir CLI - Autonomous Spec-Driven developer",
    no_args_is_help=True,
)

smoke_app = typer.Typer(
    help="Full System Smoke Test v1: журнал, seq, cli_tool/, replay sanity.",
    no_args_is_help=True,
)
app.add_typer(smoke_app, name="smoke")
app.add_typer(journal_app, name="journal")


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    log_format: str = "text",
):
    """Настраивает логирование для вывода в консоль и опционально в файл."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    text_fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    stream_handler = logging.StreamHandler()
    if log_format == "json":
        stream_handler.setFormatter(JsonFormatter())
    else:
        stream_handler.setFormatter(logging.Formatter(text_fmt))
    root_logger.addHandler(stream_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
        if log_format == "json":
            file_handler.setFormatter(JsonFormatter())
        else:
            file_handler.setFormatter(logging.Formatter(text_fmt))
        root_logger.addHandler(file_handler)


def save_results(results: dict, output_dir: str):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    results_file = output_path / "execution_results.json"

    json_output = json.dumps(
        results,
        ensure_ascii=False,
        indent=2,
        cls=CustomJSONEncoder,
    )

    results_file.write_text(json_output, encoding="utf-8")

    summary_file = output_path / "summary.yaml"
    summary = {
        "execution_summary": results.get("summary", {}),
        "success": results.get("success", False),
        "generated_files": results.get("generated_files", []),
        "timestamp": datetime.now().isoformat(),
    }
    summary_file.write_text(
        yaml.dump(summary, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )


def load_and_validate_config(config_path: Optional[str] = None) -> dict:
    """Loads, validates, and returns the configuration dictionary."""
    path = config_path or "temir_config.yaml"
    raw = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded_config = yaml.safe_load(f) or {}
                raw.update(loaded_config)
        except Exception as e:
            logging.exception(f"Error loading config file: {e}")
    try:
        model = TemirConfig(**raw)
        return model.model_dump()
    except Exception as e:
        logging.exception(f"Error validating config: {e}. Falling back to defaults.")
        return TemirConfig().model_dump()

def load_prompts_data() -> Dict[str, Any]:
    """Загружает данные промптов из prompts.yaml."""
    prompts_path = Path(__file__).parent / "prompts" / "prompts.yaml"
    if prompts_path.exists():
        try:
            with open(prompts_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logging.exception(f"Ошибка загрузки prompts.yaml: {e}")
            return {}
    else:
        logging.warning(f"prompts.yaml не найден по пути: {prompts_path}.")
        return {}


async def async_main(
    user_request: str,
    output_dir: Path,
    config: Path,
    log_level: str,
    log_file: Optional[Path],
    dry_run: bool,
    no_cache: bool,
    no_sandbox: bool,
    continue_on_failure: bool,
    collect_artifacts: bool,
    print_summary: bool,
    artifacts_include: Optional[list[str]],
    artifacts_exclude: Optional[list[str]],
    log_format: str,
    auto_plan: bool,
    skip_exec_contract: bool,
):
    """Asynchronous main function to run the development pipeline."""
    setup_logging(log_level, str(log_file) if log_file else None, log_format=log_format)
    logger = logging.getLogger(__name__)

    universal_agent_instance = None  # Инициализируем переменную для finally блока
    specialized_agents = []

    try:
        try:
            contract = run_guard_preflight(
                dry_run=dry_run,
                output_dir=output_dir,
                skip=skip_exec_contract,
            )
        except ExecutionContractError as e:
            typer.secho(
                "Execution Guard v1: среда не прошла preflight.",
                fg=typer.colors.RED,
                bold=True,
            )
            for line in e.issues:
                typer.secho(f"  • {line}", fg=typer.colors.RED)
            hints = collect_repair_hints(e.issues)
            if hints:
                typer.secho("Подсказки:", fg=typer.colors.YELLOW, bold=True)
                for h in hints:
                    typer.secho(f"  → {h}", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1) from e

        for w in contract.warnings:
            logger.warning("%s", w)

        if not contract.skipped:
            for w in gather_env_file_warnings(contract.assumptions):
                logger.warning("%s", w)
            logger.info(
                "Execution Guard v1 OK: py=%s venv=%s cwd=%s journal=%s chain=%s",
                contract.assumptions.python_version,
                contract.assumptions.virtual_env or "(none)",
                contract.assumptions.cwd,
                contract.assumptions.journal_base,
                ",".join(contract.assumptions.gemini_model_chain),
            )

        # 1. Загрузка конфигурации
        app_config = load_and_validate_config(str(config) if config.exists() else None)

        # 2. Применение переопределений
        app_config["cache_enabled"] = not no_cache
        app_config["sandbox_enabled"] = not no_sandbox
        app_config["continue_on_failure"] = continue_on_failure
        app_config["collect_artifacts"] = bool(collect_artifacts)
        app_config["log_format"] = log_format
        app_config["auto_plan"] = auto_plan
        if artifacts_include is not None:
            app_config["artifacts_include"] = artifacts_include
        if artifacts_exclude is not None:
            app_config["artifacts_exclude"] = artifacts_exclude

        # 3. Инициализация компонентов
        cache = CacheManager()
        prompts_data = load_prompts_data()
        
        # Rate Limiter: 2 requests per second, burst 10
        rate_limiter = TokenBucket(tokens_per_second=2, max_tokens=10)

        if not app_config["sandbox_enabled"]:
            typer.secho(
                "ВНИМАНИЕ: Запуск в небезопасном локальном режиме (Windows Native Friendly).",
                fg=typer.colors.YELLOW,
            )
            sandbox = LocalUnsafeSandbox(project_dir=str(output_dir))
        else:
            sandbox = DockerManager()

        tools = AgentTools(sandbox_manager=sandbox)

        # Инициализация специализированных агентов
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        mercury_api_key = os.getenv("INCEPTION_API_KEY")

        backend_coder_agent = BackendCoderAgent(
            api_key=mercury_api_key,
            tools=tools,
            rate_limiter=rate_limiter,
            prompts_data=prompts_data,
            gemini_api_key=gemini_api_key,  # Для fallback на Gemini
        )
        specialized_agents.append(backend_coder_agent)

        system_architect_agent = SystemArchitectAgent(
            api_key=gemini_api_key,
            tools=tools,
            rate_limiter=rate_limiter,
            prompts_data=prompts_data,
        )
        specialized_agents.append(system_architect_agent)
        
        tester_agent = TesterAgent(
            api_key=gemini_api_key,
            tools=tools,
            rate_limiter=rate_limiter,
            prompts_data=prompts_data,
        )
        specialized_agents.append(tester_agent)

        reviewer_agent = GeminiEnhancerAgent(
            api_key=gemini_api_key,
            tools=tools,
            rate_limiter=rate_limiter,
            prompts_data=prompts_data,
        )
        specialized_agents.append(reviewer_agent)

        supervisor_agent = SupervisorAgent(
            api_key=gemini_api_key,
            tools=tools,
            rate_limiter=rate_limiter,
            prompts_data=prompts_data,
        )
        specialized_agents.append(supervisor_agent)

        # Инициализация универсального агента-диспетчера
        universal_agent_instance = UniversalAgent(
            backend_coder_agent=backend_coder_agent,
            system_architect_agent=system_architect_agent,
            tester_agent=tester_agent,
            reviewer_agent=reviewer_agent,
            supervisor_agent=supervisor_agent,
        )

        orchestrator = Orchestrator(
            config=app_config,
            agent=universal_agent_instance, # Передаем диспетчер
            cache_manager=cache,
            sandbox_manager=sandbox,
            tools=tools,
        )

    except RuntimeError as e:
        typer.secho(f"Ошибка инициализации: {e}", fg=typer.colors.RED)
        if "Docker" in str(e):
            typer.secho(
                "Пожалуйста, запустите Docker или используйте флаг --no-sandbox.",
                fg=typer.colors.YELLOW,
            )
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("=== РЕЖИМ DRY RUN ===")
        typer.echo(f"Запрос пользователя: {user_request}")
        # В режиме dry_run тоже нужно закрыть агента
        if universal_agent_instance:
            await universal_agent_instance.close()
        raise typer.Exit()

    typer.echo("🚀 Запуск Оркестратора...")
    if sys.platform == "win32":
        typer.echo("🔧 Platform: Windows (Optimized: IOCP/Proactor Enabled)")

    start = datetime.now()
    
    try:
        results = await orchestrator.execute_full_pipeline(
            user_request=user_request,
            output_dir=str(output_dir),
        )
    finally:
        # ВАЖНО: Закрываем постоянные HTTP соединения агентов
        if universal_agent_instance:
            await universal_agent_instance.close()
        for agent_instance in specialized_agents:
            await agent_instance.close()


    end = datetime.now()
    save_results(results, str(output_dir))

    rid = results.get("run_id")
    if rid:
        safe_rid = sanitize_run_id(str(rid))
        typer.echo(
            f"Event journal run_id: {rid} (JSONL: .andromeda/runs/{safe_rid}/events.jsonl)",
        )
        typer.echo(f"События в терминале (без UI): temir journal tail -r {safe_rid}")

    success = results.get("success", False)
    color = typer.colors.GREEN if success else typer.colors.RED
    status = "✅ УСПЕШНО" if success else "❌ С ОШИБКАМИ"

    typer.secho(f"\nЗавершено. Статус: {status}", fg=color, bold=True)
    typer.echo(f"Продолжительность: {end - start}")
    
    summary = results.get("summary") or {}
    if summary:
        typer.echo(
            f"Задач: {summary.get('total_tasks', 0)} | "
            f"Успешно: {summary.get('completed', 0)} | "
            f"Провалено: {summary.get('failed', 0)}",
        )
        total_cost = summary.get('total_cost_usd', 0.0)
        typer.echo(f"Общая стоимость: ${total_cost:.6f} USD")
    
    if orchestrator.config.get("collect_artifacts"):
        gen_files = results.get("generated_files") or []
        typer.echo(f"Артефактов собрано: {len(gen_files)}")
    
    if print_summary:
        typer.echo(
            json.dumps(results, ensure_ascii=False, indent=2, cls=CustomJSONEncoder),
        )


@app.command()
def run(
    user_request: Optional[str] = typer.Argument(
        None,
        help="Текст запроса; или укажите --request-file (удобно для spec.yaml в cmd.exe)",
    ),
    output_dir: Path = typer.Option(
        "./output",
        "-o",
        "--output-dir",
        help="Директория результатов",
    ),
    request_file: Optional[Path] = typer.Option(
        None,
        "--request-file",
        "-f",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Прочитать запрос из файла UTF-8 (например spec.yaml); не смешивать с позиционным текстом",
    ),
    config: Path = typer.Option("temir_config.yaml", help="Путь к конфигу"),
    log_level: str = typer.Option("INFO", help="Уровень логирования"),
    log_file: Optional[Path] = typer.Option("log.txt", help="Файл лога"),
    dry_run: bool = typer.Option(False, help="Dry run"),
    no_cache: bool = typer.Option(False, help="Отключить кэш"),
    no_sandbox: bool = typer.Option(False, help="Отключить Docker (Windows friendly)"),
    continue_on_failure: bool = typer.Option(False, help="Продолжать при ошибках"),
    collect_artifacts: bool = typer.Option(False, help="Собирать артефакты"),
    print_summary: bool = typer.Option(False, help="Печатать JSON в stdout"),
    artifacts_include: Optional[list[str]] = typer.Option(None, help="Include patterns"),
    artifacts_exclude: Optional[list[str]] = typer.Option(None, help="Exclude patterns"),
    log_format: str = typer.Option("text", help="Format: text|json"),
    auto_plan: bool = typer.Option(False, help="Автогенерация плана"),
    skip_exec_contract: bool = typer.Option(
        False,
        "--skip-exec-contract",
        help="Пропустить preflight Execution Contract (не рекомендуется).",
    ),
):
    """Запускает полный цикл разработки."""
    if request_file is not None:
        if user_request is not None:
            typer.secho(
                "Укажите либо текст запроса (аргумент), либо --request-file, не оба сразу.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        user_request = request_file.read_text(encoding="utf-8")
    if not user_request or not str(user_request).strip():
        typer.secho(
            "Нужен текст запроса: temir run \"...\" ИЛИ temir run --request-file spec.yaml",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    # WINDOWS OPTIMIZATION: Включаем самый быстрый Event Loop для Windows
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    asyncio.run(async_main(
        user_request,
        output_dir,
        config,
        log_level,
        log_file,
        dry_run,
        no_cache,
        no_sandbox,
        continue_on_failure,
        collect_artifacts,
        print_summary,
        artifacts_include,
        artifacts_exclude,
        log_format,
        auto_plan,
        skip_exec_contract,
    ))


@smoke_app.command("print-prompt")
def smoke_print_prompt_cmd():
    """Печатает канонический prompt для smoke (тот же текст — в temir/prompts/full_system_smoke_v1.txt)."""
    typer.echo(read_smoke_prompt())


@smoke_app.command("prompt-path")
def smoke_prompt_path_cmd():
    """Путь к файлу smoke-prompt (для внешних скриптов)."""
    typer.echo(str(smoke_prompt_file()))


@smoke_app.command("instructions")
def smoke_instructions_cmd():
    """Как прогнать Devin-style smoke вручную."""
    typer.echo(
        "1) temir guard --full   (или: temir doctor --full)\n"
        "2) Самый простой запуск smoke-промпта: temir smoke run -o .\\output --no-sandbox\n"
        "   (не используйте литерал $p в cmd — это не подставит текст; в PowerShell: "
        "$p = Get-Content (temir smoke prompt-path) -Raw; temir run $p -o .\\output --no-sandbox)\n"
        "3) temir ui — загрузить run_id из .andromeda/runs\n"
        "4) temir smoke validate -o .\\output --pytest\n",
    )


@smoke_app.command("run")
def smoke_run_cmd(
    output_dir: Path = typer.Option(
        Path("./output"),
        "-o",
        "--output-dir",
        help="Директория результатов",
    ),
    config: Path = typer.Option("temir_config.yaml", "--config", help="Конфиг"),
    log_level: str = typer.Option("INFO", "--log-level"),
    log_file: Optional[Path] = typer.Option(Path("log.txt"), "--log-file"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    no_sandbox: bool = typer.Option(False, "--no-sandbox"),
    continue_on_failure: bool = typer.Option(False, "--continue-on-failure"),
    collect_artifacts: bool = typer.Option(False, "--collect-artifacts"),
    print_summary: bool = typer.Option(False, "--print-summary"),
    log_format: str = typer.Option("text", "--log-format"),
    auto_plan: bool = typer.Option(False, "--auto-plan"),
    skip_exec_contract: bool = typer.Option(False, "--skip-exec-contract"),
):
    """Запуск полного pipeline с текстом из temir/prompts/full_system_smoke_v1.txt (без shell-переменных)."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    prompt = read_smoke_prompt()
    typer.echo(f"Smoke prompt загружен ({len(prompt)} символов).")
    asyncio.run(
        async_main(
            prompt,
            output_dir,
            config,
            log_level,
            log_file,
            False,
            no_cache,
            no_sandbox,
            continue_on_failure,
            collect_artifacts,
            print_summary,
            None,
            None,
            log_format,
            auto_plan,
            skip_exec_contract,
        ),
    )


@smoke_app.command("validate")
def smoke_validate_cmd(
    run_id: Optional[str] = typer.Option(
        None,
        "--run-id",
        "-r",
        help="Имя каталога run под journal (как в .andromeda/runs/<id>). По умолчанию — последний run.",
    ),
    journal: Optional[Path] = typer.Option(
        None,
        "--journal",
        "-j",
        help="Путь к events.jsonl (вместо --run-id).",
    ),
    output_dir: Path = typer.Option(Path("./output"), "-o", help="Каталог артефактов пайплайна"),
    pytest_run: bool = typer.Option(False, "--pytest", help="Запустить pytest в output/cli_tool/tests"),
    strict_recommended: bool = typer.Option(
        False,
        "--strict-recommended",
        help="Считать FAIL при отсутствии recommended топиков.",
    ),
):
    """Проверяет events.jsonl и дерево cli_tool/ после smoke-run."""
    resolved_id: Optional[str] = run_id
    jpath: Optional[Path] = None
    if journal is not None:
        if not journal.is_file():
            typer.secho(f"Journal not found: {journal}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        events = load_events_jsonl(journal)
        jpath = journal
        if resolved_id is None and events:
            r0 = events[0].get("run_id")
            if r0 is not None:
                resolved_id = str(r0)
    else:
        if not resolved_id:
            ids = list_run_ids()
            if not ids:
                typer.secho("Нет run в journal: задайте --run-id или --journal.", fg=typer.colors.RED)
                raise typer.Exit(code=1)
            resolved_id = ids[-1]
            typer.echo(f"Using latest run_id: {resolved_id}")
        events = load_run_events(resolved_id)
        safe = sanitize_run_id(resolved_id)
        jpath = get_journal_base() / safe / "events.jsonl"

    report = build_report(
        events,
        run_id=resolved_id,
        journal_path=jpath,
        output_dir=output_dir,
        run_pytest=pytest_run,
        strict_recommended=strict_recommended,
    )
    for line in report.messages():
        typer.echo(line)
    raise typer.Exit(code=0 if report.passed else 1)


def _execution_guard_cli(
    output_dir: Path,
    full: bool,
    check_imports: bool,
) -> None:
    """Общая реализация для `temir guard` и `temir doctor`."""
    if check_imports:
        import_errors = check_runtime_imports()
        if import_errors:
            typer.secho("Импорты: ошибки", fg=typer.colors.RED, bold=True)
            for line in import_errors:
                typer.secho(f"  • {line}", fg=typer.colors.RED)
            raise typer.Exit(code=1)

    try:
        contract = run_guard_preflight(dry_run=not full, output_dir=output_dir, skip=False)
    except ExecutionContractError as e:
        typer.secho("Execution Guard v1: preflight не пройден.", fg=typer.colors.RED, bold=True)
        for line in e.issues:
            typer.secho(f"  • {line}", fg=typer.colors.RED)
        hints = collect_repair_hints(e.issues)
        if hints:
            typer.secho("Подсказки:", fg=typer.colors.YELLOW, bold=True)
            for h in hints:
                typer.secho(f"  → {h}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from e

    for w in contract.warnings:
        typer.secho(f"  ! {w}", fg=typer.colors.YELLOW)
    for w in gather_env_file_warnings(contract.assumptions):
        typer.secho(f"  ! {w}", fg=typer.colors.YELLOW)

    typer.secho("Runtime snapshot (kernel execution context):", bold=True)
    for line in format_assumptions_lines(contract.assumptions):
        typer.echo(f"  {line}")

    typer.secho("Execution Guard v1: OK", fg=typer.colors.GREEN, bold=True)


@app.command("guard")
def guard_cmd(
    output_dir: Path = typer.Option(Path("./output"), "-o", help="Каталог результатов (проверка записи)"),
    full: bool = typer.Option(
        False,
        "--full",
        help="Полный preflight как у temir run (GEMINI_API_KEY, журнал, цепочка моделей).",
    ),
    check_imports: bool = typer.Option(
        False,
        "--check-imports",
        help="Проверить импорт ключевых пакетов (typer, google.generativeai, …).",
    ),
):
    """Execution Guard v1: preflight и снимок runtime без запуска пайплайна."""
    _execution_guard_cli(output_dir, full, check_imports)


@app.command("doctor")
def doctor_cmd(
    output_dir: Path = typer.Option(Path("./output"), "-o", help="Каталог результатов (проверка записи)"),
    full: bool = typer.Option(
        False,
        "--full",
        help="Полный preflight как у temir run (GEMINI_API_KEY, журнал, цепочка моделей).",
    ),
    check_imports: bool = typer.Option(
        False,
        "--check-imports",
        help="Проверить импорт ключевых пакетов (typer, google.generativeai, …).",
    ),
):
    """Синоним `temir guard` — диагностика среды перед run."""
    _execution_guard_cli(output_dir, full, check_imports)


@app.command()
def summary(
    output_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    full: bool = typer.Option(False, help="Показать полный JSON"),
):
    """Печатает сводку результатов."""
    results_path = output_dir / "execution_results.json"
    if not results_path.exists():
        typer.secho(f"Файл не найден: {results_path}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    data = json.loads(results_path.read_text(encoding="utf-8"))
    if full:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, cls=CustomJSONEncoder))
        return
    summary_obj = data.get("summary") or {}
    success = data.get("success")
    status = "SUCCESS" if success else "FAILED"
    typer.echo(f"Status: {status}")
    typer.echo(
        f"Tasks total: {summary_obj.get('total_tasks', 0)}, "
        f"completed: {summary_obj.get('completed', 0)}, "
        f"failed: {summary_obj.get('failed', 0)}",
    )


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Адрес привязки (только local по умолчанию)"),
    port: int = typer.Option(8756, help="Порт (как в spec interface_layer)"),
):
    """Debug Control Panel: Web UI (pipeline, логи, diff, cost) — REST + WebSocket."""
    try:
        import uvicorn
    except ImportError as e:
        typer.secho(
            "Нужны зависимости: pip install fastapi 'uvicorn[standard]'",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1) from e
    from temir.web.app import create_app

    typer.secho(f"Temir Debug Panel: http://{host}:{port}/", fg=typer.colors.GREEN)
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


@app.command()
def config_cmd(
    config_path: Optional[Path] = typer.Option(None, help="Путь к temir_config.yaml"),
):
    """Печатает активную конфигурацию."""
    try:
        conf = load_and_validate_config(str(config_path) if config_path else None)
        typer.echo(yaml.dump(conf, allow_unicode=True, sort_keys=True))
    except Exception as e:
        logging.error(f"Ошибка конфига: {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()