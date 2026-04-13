# Temir — AI Development Ecosystem

**Temir** — это автономная, мультиагентная и саморазвивающаяся платформа для разработки ПО. Она способна создавать, тестировать и развертывать программные продукты на основе высокоуровневых спецификаций, используя мощь больших языковых моделей (LLM).

Это уже не просто MVP. Система прошла путь до зрелой платформы с фокусом на надежность, прозрачность и интеллект.

## Ключевые Возможности

-   **Спецификация как Код:** Управляйте всем процессом разработки через один `spec.yaml` файл.
-   **Автономный Планировщик:** Не хотите писать детальный план? Просто опишите цель, и AI-планировщик (`--auto-plan`) сгенерирует план выполнения за вас.
-   **Мультиагентная Система:** Специализированные AI-агенты (`CODER`, `TESTER`, `REVIEWER`) работают вместе для достижения цели.
-   **Безопасная Песочница:** Весь код выполняется в изолированном Docker-контейнере для максимальной безопасности.
-   **Система Памяти:** Temir учится на своем опыте. Успешные планы кэшируются в локальной SQLite базе, что ускоряет повторные запуски и экономит API-вызовы.
-   **Прозрачность и Диагностика:** Детальные JSON-отчеты, структурированные логи и удобные CLI-команды для анализа конфигурации и результатов.
-   **Кросс-платформенность:** Гарантированная работа на Windows, macOS и Linux благодаря CI-тестированию.
-   **Надежность:** Устойчивый парсинг ответов от AI, автоматические повторы запросов при ошибках квот и наличие оффлайн-режима.
-   **Level 2 — исполнение без двусмысленности:** один gate перед запуском инструмента, политика retry по кодам ошибок, проверка закрытия уровня DAG и идемпотентность шагов по `intent_sha256` (replay/retry без дублей).

## Требования

-   Python 3.9+
-   Docker Engine (рекомендуется для безопасной работы)
-   API-ключ Google Gemini (или другой совместимой модели)

## Установка

1.  **Создайте и активируйте виртуальное окружение:**
    ```bash
    python -m venv .venv
    # Windows (PowerShell)
    .\.venv\Scripts\Activate.ps1
    # macOS / Linux
    source .venv/bin/activate
    ```

2.  **Установите Temir в режиме редактирования:**
    ```bash
    pip install -e .
    ```

3.  **Установите все зависимости:**
    ```bash
    # (Опционально) Создайте файл requirements.txt, если его нет
    # pip freeze > requirements.txt
    pip install pydantic PyYAML docker google-generativeai typer[all] rich pytest ruff
    ```

## Настройка

1.  **Настройте API-ключ:**
    Установите переменную окружения `GEMINI_API_KEY`:
    ```bash
    # Windows (PowerShell)
    $env:GEMINI_API_KEY="ВАШ_КЛЮЧ"

    # macOS / Linux
    export GEMINI_API_KEY="ВАШ_КЛЮЧ"
    ```

2.  **(Опционально) Настройте `temir_config.yaml`:**
    Создайте в корне проекта файл `temir_config.yaml` для тонкой настройки. Вы можете использовать его для установки таймаутов, отключения кэша по умолчанию и т.д.

3.  **(Опционально) Переменные окружения оркестрации и журнала:**

    | Переменная | Назначение |
    |------------|------------|
    | `TEMIR_STEP_IDEMPOTENCY` | `1` (по умолчанию) — не выполнять повторно шаг с тем же `intent_sha256` в рамках прогона; `0` / `false` — отключить. |
    | `TEMIR_EVENT_JOURNAL_DIR` | Каталог для JSONL-журналов ранов; иначе используется `.andromeda/runs/`. |
    | `TEMIR_EVENT_SCHEMA_STRICT` | Строгая проверка полей событий при публикации (`1` / `true`). |
    | `TEMIR_CAPABILITY_ALLOWLIST` | Список разрешённых capability-токенов через запятую (см. `temir.core.capabilities`). |

## Использование CLI

Temir предоставляет мощный и удобный интерфейс командной строки.

### Основные Команды

```bash
# Показать помощь по всем командам
temir --help

# 1. Проверить активную конфигурацию
temir config

# 2. Запуск пайплайна: текст запроса строкой или файлом
temir run "Кратко опишите задачу для оркестратора" -o ./output

# Взять запрос из spec.yaml (работает в cmd.exe и PowerShell):
temir run --request-file spec.yaml -o ./output --no-sandbox

# Только в PowerShell: подставить содержимое файла в аргумент
# temir run (Get-Content spec.yaml -Raw) -o ./output --no-sandbox

# 3. Журнал событий без Web UI
temir journal runs
temir journal tail -f

# 4. Проанализировать результаты последнего запуска
temir summary ./output
```

### Команда `run` и ее флаги

Каталог результатов: **`-o`** или **`--output-dir`** (одно и то же).

```bash
# Запустить стандартное выполнение (текст запроса в кавычках)
temir run "Ваша задача..." --output-dir ./my_project

# Активировать AI-планировщик (если полагаетесь на автогенерацию плана из запроса)
temir run "Задача..." --auto-plan -o ./output

# Запустить без Docker (Windows: локальная ФС, небезопасно)
temir run "Задача..." --no-sandbox -o ./output

# Отключить кэширование планов
temir run "Задача..." --no-cache -o ./output

# Собирать артефакты (например, только .py и .md файлы)
temir run "Задача..." --collect-artifacts --artifacts-include "*.py" "*.md" -o ./output

# Записать детальный лог
temir run "Задача..." --log-format json --log-file temir_run.log -o ./output
```

## Формат `spec.yaml`

Это сердце системы. Вы можете либо детально описать план, либо положиться на AI-планировщик.

**Пример с детальным планом:**
```yaml
project:
  name: "SimpleWebApp"
  language: "Python"

execution_plan:
  - id: "create_structure"
    description: "Create directories 'src' and 'tests'"
    executor: "CODER"
  
  - id: "generate_code"
    description: "Create the file 'src/main.py' with a basic FastAPI hello world app."
    executor: "CODER"
    dependencies: ["create_structure"]
  
  - id: "generate_tests"
    description: "Create a test file 'tests/test_main.py' for 'src/main.py'."
    executor: "CODER"
    dependencies: ["generate_code"]

  - id: "run_tests"
    description: "Install dependencies (fastapi, uvicorn, pytest) and run tests."
    executor: "TESTER"
    dependencies: ["generate_tests"]
```

**Пример для AI-планировщика (`--auto-plan`):**
```yaml
project:
  name: "SimpleWebApp"
  language: "Python"
  description: "A basic FastAPI application with a single endpoint that returns 'Hello, World'."

# execution_plan пуст. AI сгенерирует его на основе project.description
execution_plan: []
```

## Результаты Выполнения

Все результаты сохраняются в выходную директорию (по умолчанию `./output`):

-   `execution_results.json`: Подробный JSON-отчет со всеми шагами, статусами, логами и временем выполнения каждой задачи.
-   `summary.yaml`: Краткая YAML-сводка для быстрого обзора.

Вы можете легко проанализировать результаты с помощью команды `temir summary <output_dir>`.

Эти файлы и каталог `./output` по умолчанию **не предназначены для Git**: они перечислены в `.gitignore` (песочница, `execution_results.json`, `summary.yaml`, локальные логи). В репозиторий имеет смысл коммитить спецификации и код Temir, а не состояние последнего прогона.

## Статус реализации и следующие шаги

### Реализовано (Level 2 закрыт)

На уровне оркестрации и DAG-исполнения сделано следующее:

- **Single gate** — `can_execute_tool_step`: capabilities, preflight (ОС и shell), проверка идемпотентности; решение в виде `ExecutionDecision` (allowed, reason, retryable, terminal, skipped_idempotent).
- **Retry как политика** — таблица кодов для IR-контракта и preflight (`temir.core.retry_policy`): что уходит в repair loop, что terminal; поведение не сводится к полю `meta.retryable` в IR.
- **Пост-валидация уровня** — после каждого уровня графа вызывается `validate_level_completion`: полное множество шагов уровня, каждый в закрытом состоянии (`completed`), при включённой идемпотентности — без дубликатов успешного `intent_sha256`.
- **Идемпотентность** — на прогон ведётся множество выполненных `(task_id, intent_sha256)`; повторный intent даёт skip и событие с `idempotent_skip`; после реального успеха intent регистрируется.
- **Платформа** — единый `PlatformContext` и согласованные события/политика для Windows и Unix-shell.
- **CLI и наблюдаемость** — `temir journal runs`, `tail`, `cat`; запуск из файла `--request-file` / `-f`; каталог результатов `-o` и `--output-dir`.
- **Git** — `.gitignore` покрывает `output/`, артефакты прогона, `.env`, кэши и журналы по умолчанию.

Тесты для этого слоя: `tests/test_level2_closure.py` и общий прогон `pytest tests`.

### Что делать дальше

1. **Level 3 (минимальный intelligence core)** — по желанию: строгий компилятор spec → IR со схемой, слой нормализации execution intent, небольшой model router (без раздувания архитектуры).
2. **Спецификация продукта** — `spec.yaml` в репозитории описывает целевую экосистему; процент «готовности спека» и реализация фич (Flutter, расширенный IR и т.д.) — отдельная дорожная карта от Level 2.
3. **Эксплуатация** — держать секреты в `.env`, не коммитить `./output`; при строгой схеме событий проверять совместимость новых полей в `temir.web.event_schema`; при кастомном каталоге журнала задать `TEMIR_EVENT_JOURNAL_DIR` и при необходимости добавить его в `.gitignore`.
4. **Качество** — перед изменениями: `ruff check .`, `pytest`.

## Архитектура и Философия

-   **Dependency Injection:** Модули получают зависимости через конструктор, что обеспечивает слабую связность и высокую тестируемость.
-   **Composition Root:** `temir/main.py` является единственной точкой, где собираются и конфигурируются все компоненты системы.
-   **Надежность по умолчанию:** Система спроектирована быть отказоустойчивой: от парсинга ответов AI до автоматических повторов запросов к API.
-   **Прозрачность:** Валидация конфигурации, структурированные логи и детальные отчеты делают каждый шаг системы наблюдаемым.

## Разработка и Тестирование

```bash
# Запустить линтер и проверку стиля
ruff check .

# Запустить полный набор тестов
pytest
```

Критические пути покрыты тестами, в том числе:
-   Интеграцию кэша (hit/miss).
-   Работу пайплайна оркестратора (успех, провал, пропуск зависимостей).
-   Оффлайн-режим агента.
-   Работу авто-планировщика.
-   Структуру итогового JSON-отчета (снэпшот-тест).
-   Level 2: gate, политика retry, валидация уровня, идемпотентность (`tests/test_level2_closure.py`).

CI/CD настроен через GitHub Actions для автоматической проверки на **Ubuntu, Windows и macOS**.

## Лицензия

MIT License