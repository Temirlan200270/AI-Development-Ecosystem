import logging
import platform
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

import yaml

from temir.core.patch_manager import PatchManager
from temir.sandbox.local_sandbox import LocalUnsafeSandbox
from temir.sandbox.validation import ValidationPipeline

logger = logging.getLogger(__name__)


class AgentTools:
    """
    Инструменты для AI-агента (Toolbox).
    Предоставляет высокоуровневые методы для работы с ФС и выполнения команд.
    Robust Edition: Устойчив к ошибкам в аргументах от LLM.
    """

    def __init__(self, sandbox_manager: Any):
        self.temp_files = []
        self.sandbox = sandbox_manager
        self.patch_manager = PatchManager()

    def execute_shell(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        """
        Выполнить shell-команду.
        Адаптирует 'python3' -> 'python' для Windows.
        """
        if platform.system() == "Windows":
            command = command.replace("python3 ", "python ").replace("python3\n", "python\n")
            command = re.sub(r"\bpython3\b", "python", command)

        logger.info(f"EXEC: {command[:100]}...")
        if not self.sandbox:
            return {"success": False, "stderr": "Песочница не подключена"}
        
        return self.sandbox.execute_command(command, timeout)

    def write_file(self, content: str, path: str, overwrite: bool = True) -> bool:
        """
        Записать файл в песочницу.
        SMART: Создает родительские папки и __init__.py ТОЛЬКО если пишем .py файл.
        """
        logger.info(f"WRITE: {path}")
        if not self.sandbox:
            return False
            
        if not overwrite and self.file_exists(path):
            logger.warning(f"write_file: файл уже существует и overwrite=False: {path}")
            return False

        # Нормализация пути
        norm_path = path.replace("\\", "/")
        
        # Логика создания родительских директорий
        if "/" in norm_path:
            parent_dir = "/".join(norm_path.split("/")[:-1])
            if parent_dir and not self.directory_exists(parent_dir):
                # SMART CHECK: Если мы пишем .py файл, значит это Python-пакет
                is_python_file = norm_path.endswith(".py")
                self.create_directory(dir_path=parent_dir, is_python_package=is_python_file)

        return self.sandbox.write_file_to_sandbox(content, path)

    def create_directory(self, dir_path: str = None, path: str = None, is_python_package: Optional[bool] = None, **kwargs) -> bool:
        """
        Создать директорию.
        HOTFIX: Поддерживает 'dir_path', 'path' и игнорирует лишние аргументы.
        """
        # Выбираем непустой аргумент (алиасы)
        target_path = dir_path or path
        if not target_path:
            logger.error("create_directory: Не передан путь (ни dir_path, ни path)")
            return False

        logger.info(f"MKDIR: {target_path}")
        
        # 1. Создаем директорию
        command = (
            f'python -c "import pathlib,sys; '
            f'pathlib.Path(sys.argv[1]).mkdir(parents=True, exist_ok=True)" '
            f'"{target_path}"'
        )
        result = self.execute_shell(command)
        if not result.get("success", False):
            return False

        # 2. Решаем, нужен ли __init__.py
        should_create_init = False
        
        if is_python_package is True:
            should_create_init = True
        elif is_python_package is False:
            should_create_init = False
        else:
            # Если явно не сказано, проверяем корень проекта
            should_create_init = self._is_python_project_root()

        if should_create_init:
            norm_path = target_path.replace("\\", "/")
            name = norm_path.rstrip("/").split("/")[-1]
            
            # Исключения
            ignore_dirs = {
                ".git", ".idea", ".vscode", "__pycache__", ".venv", "venv", "env",
                "logs", "output", "docs", "assets", "cache", "node_modules", "build", "dist", "tests"
            }
            
            if not name.startswith(".") and name not in ignore_dirs:
                init_file = f"{target_path}/__init__.py"
                if not self.file_exists(init_file):
                    logger.info(f"AUTO-FIX: Обнаружен Python контекст. Создаю __init__.py в {target_path}")
                    self.sandbox.write_file_to_sandbox("", init_file)
        
        return True

    def _is_python_project_root(self) -> bool:
        """Эвристика: проверяет наличие маркеров Python-проекта."""
        markers = ["pyproject.toml", "requirements.txt", "setup.py", "Pipfile"]
        for marker in markers:
            if self.file_exists(marker):
                return True
        return False

    def smart_patch(self, path: str, patch_text: str) -> bool:
        """
        Применяет патч к файлу, используя 'fuzzy patching'.
        """
        logger.info(f"SMART_PATCH: {path}")
        
        read_result = self.read_file(path)
        if not read_result.get("success") or read_result.get("content") is None:
            logger.error(f"Patch failed: File not found at {path}")
            return False
        
        original_content = read_result["content"]

        new_content, results = self.patch_manager.apply_patch(patch_text, original_content)

        if not all(results):
            logger.error(f"Patch application failed for {path}. Results: {results}")
            return False

        if new_content == original_content:
            logger.warning(f"Patch resulted in no changes to the file {path}.")
            # Может быть не ошибкой, если патч пустой или уже применен
            return True

        return self.write_file(path, new_content)

    def read_file(self, path: str) -> Dict[str, Any]:
        """
        Читает содержимое файла.
        Возвращает словарь для совместимости с orchestrator.
        """
        logger.info(f"READ: {path}")
        if not self.sandbox:
            return {"success": False, "error": "Песочница не подключена", "content": None}
        
        content = self.sandbox.read_file_from_sandbox(path)
        if content is None:
            return {
                "success": False,
                "error": f"Файл не найден: {path}",
                "content": None,
            }
        
        return {
            "success": True,
            "content": content,
            "output": content,
        }

    def list_directory(self, dir_path: str = ".", path: str = None) -> Dict[str, Any]:
        """
        Список файлов в директории.
        Поддерживает оба аргумента: dir_path и path (для совместимости с LLM).
        Возвращает словарь для совместимости с orchestrator.
        """
        target_path = path or dir_path
        command = (
            f'python -c "import pathlib,sys; '
            f"base=pathlib.Path(sys.argv[1]); "
            f'[print(p.name) for p in base.iterdir()]" '
            f'"{target_path}"'
        )
        result = self.execute_shell(command)
        if result.get("success"):
            files = result.get("stdout", "").splitlines()
            return {
                "success": True,
                "files": files,
                "output": "\n".join(files),
                "stdout": result.get("stdout", ""),
            }
        else:
            return {
                "success": False,
                "error": result.get("stderr", "Failed to list directory"),
                "files": [],
                "output": "",
            }

    def run_linter(self, path: str = ".") -> Dict[str, Any]:
        logger.info(f"LINT: {path}")
        return ValidationPipeline(self.sandbox).run_linter(path)

    def run_tests(self, path: str = "tests", **kwargs) -> Dict[str, Any]:
        """
        Запускает тесты (pytest).
        HOTFIX: Принимает **kwargs, чтобы игнорировать выдуманные аргументы (напр. command).
        """
        # Если агент передал путь через 'path' или 'target', используем его, иначе дефолт
        target_path = kwargs.get("path", path)
        
        logger.info(f"TEST: {target_path}")

        if isinstance(self.sandbox, LocalUnsafeSandbox):
            try:
                # Запускаем через модуль pytest
                command = [sys.executable, "-m", "pytest", target_path]
                process = subprocess.run(
                    command, check=False, capture_output=True, text=True, encoding="utf-8",
                    cwd=self.sandbox.project_path
                )
                return {
                    "success": process.returncode == 0,
                    "stdout": process.stdout,
                    "stderr": process.stderr,
                    "exit_code": process.returncode
                }
            except Exception as e:
                return {"success": False, "stderr": f"Local pytest failed: {e}"}
        
        return ValidationPipeline(self.sandbox).run_tests(target_path)

    def file_exists(self, filename: str) -> bool:
        command = f'python -c "import pathlib,sys; sys.exit(0 if pathlib.Path(sys.argv[1]).is_file() else 1)" "{filename}"'
        return bool(self.execute_shell(command).get("success", False))

    def directory_exists(self, dir_path: str) -> bool:
        command = f'python -c "import pathlib,sys; sys.exit(0 if pathlib.Path(sys.argv[1]).is_dir() else 1)" "{dir_path}"'
        return bool(self.execute_shell(command).get("success", False))

    def remove_path(self, path: str) -> bool:
        command = (
            f'python -c "import pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); '
            f'(shutil.rmtree(p) if p.is_dir() else (p.unlink(missing_ok=True) if p.exists() else None))" "{path}"'
        )
        return self.execute_shell(command).get("success", False)

    def copy_path(self, source: str, destination: str) -> bool:
        command = (
            f'python -c "import pathlib,shutil,sys; src=pathlib.Path(sys.argv[1]); dst=pathlib.Path(sys.argv[2]); '
            f'if src.is_dir(): (shutil.rmtree(dst) if dst.exists() else None); shutil.copytree(src, dst) '
            f'else: dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)" "{source}" "{destination}"'
        )
        return self.execute_shell(command, timeout=300).get("success", False)

    def append_file(self, path: str, content: str) -> bool:
        safe_content = content.replace('"""', r"\"\"\"")
        command = (
            f'python -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
            f"p.parent.mkdir(parents=True, exist_ok=True); open(p, 'a', encoding='utf-8').write(sys.argv[2])\" "
            f'"{path}" "{safe_content}"'
        )
        return self.execute_shell(command).get("success", False)

    def install_package(self, package_name: str) -> Dict[str, Any]:
        logger.info(f"INSTALL: {package_name}")
        return self.execute_shell(f"pip install {package_name}", timeout=300)

    def get_system_info(self) -> Dict[str, Any]:
        res = self.execute_shell("python --version")
        return {"python": res.get("stdout", "").strip()}

    def git_init(self) -> Dict[str, Any]:
        """Инициализирует новый репозиторий Git."""
        logger.info("GIT: Инициализация репозитория...")
        result = self.execute_shell("git init")
        if not result.get("success", False):
            # Проверяем, может быть репозиторий уже инициализирован
            stderr = (result.get("stderr") or "").lower()
            stdout = (result.get("stdout") or "").lower()
            if ".git" in stderr or ".git" in stdout or "already exists" in stderr or "уже существует" in stderr:
                logger.info("GIT: Репозиторий уже инициализирован или частично создан")
                return {"success": True, "stdout": result.get("stdout", ""), "stderr": result.get("stderr", "")}
            # Если git не найден, делаем операцию необязательной (продолжаем без Git)
            error_msg = result.get("stderr") or ""
            if error_msg and any(phrase in error_msg.lower() for phrase in ["not found", "не является", "не распознан", "не найден", "command not found"]):
                logger.warning("GIT: Git не установлен или не найден в PATH. Продолжаем без версионного контроля.")
                # Возвращаем успех, но с предупреждением, чтобы не блокировать пайплайн
                return {
                    "success": True, 
                    "warning": "Git не установлен. Версионный контроль отключен.",
                    "stdout": result.get("stdout", ""), 
                    "stderr": result.get("stderr", "")
                }
        return result

    def git_add(self, files: List[str]) -> Dict[str, Any]:
        """Добавляет файлы в индекс Git."""
        file_list = " ".join([f'"{f}"' for f in files])
        logger.info(f"GIT: Добавление файлов в индекс: {file_list}")
        result = self.execute_shell(f"git add {file_list}")
        
        if not result.get("success", False):
            # Проверяем, может быть Git не установлен
            error_msg = (result.get("stderr") or "").lower()
            if any(phrase in error_msg for phrase in ["not found", "не является", "не распознан", "не найден", "command not found", "not a git repository"]):
                logger.warning("GIT: Git не установлен или репозиторий не инициализирован. Пропускаем git_add.")
                return {
                    "success": True,
                    "warning": "Git недоступен. Операция пропущена.",
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", "")
                }
        
        return result

    def git_commit(self, message: str) -> Dict[str, Any]:
        """Фиксирует изменения в Git с указанным сообщением."""
        logger.info(f"GIT: Коммит с сообщением: '{message}'")
        result = self.execute_shell(f'git commit -m "{message}"')
        
        if not result.get("success", False):
            # Проверяем различные причины ошибки
            error_msg = (result.get("stderr") or "").lower()
            stdout_msg = (result.get("stdout") or "").lower()
            
            # Git не установлен
            if any(phrase in error_msg for phrase in ["not found", "не является", "не распознан", "не найден", "command not found"]):
                logger.warning("GIT: Git не установлен. Пропускаем git_commit.")
                return {
                    "success": True,
                    "warning": "Git не установлен. Коммит пропущен.",
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", "")
                }
            
            # Репозиторий не инициализирован
            if "not a git repository" in error_msg or "not a git repository" in stdout_msg:
                logger.warning("GIT: Репозиторий не инициализирован. Пропускаем git_commit.")
                return {
                    "success": True,
                    "warning": "Git репозиторий не инициализирован. Коммит пропущен.",
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", "")
                }
            
            # Нет изменений для коммита
            if "nothing to commit" in error_msg or "nothing to commit" in stdout_msg:
                logger.info("GIT: Нет изменений для коммита.")
                return {
                    "success": True,
                    "warning": "Нет изменений для коммита.",
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", "")
                }
        
        return result

    def git_status(self) -> Dict[str, Any]:
        """Проверяет статус репозитория Git."""
        logger.info("GIT: Проверка статуса репозитория...")
        result = self.execute_shell("git status")
        return {"success": result.get("success", False), "output": result.get("stdout", "")}
    
    def git_diff(self) -> Dict[str, Any]:
        """Показывает изменения между рабочей директорией и индексом Git."""
        logger.info("GIT: Проверка изменений...")
        result = self.execute_shell("git diff")
        return {"success": result.get("success", False), "output": result.get("stdout", "")}

    def cleanup(self):
        pass