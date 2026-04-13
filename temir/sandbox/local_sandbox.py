# src/temir/sandbox/local_sandbox.py
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class LocalUnsafeSandbox:
    """
    НЕБЕЗОПАСНАЯ локальная песочница, выполняющая команды напрямую
    в файловой системе. Использовать только для доверенной разработки!
    """

    def __init__(self, project_dir: str):
        self.project_path = Path(project_dir).resolve()
        self.project_path.mkdir(parents=True, exist_ok=True)
        logger.warning(
            f"!!! ЗАПУЩЕНА НЕБЕЗОПАСНАЯ ЛОКАЛЬНАЯ ПЕСОЧНИЦА в {self.project_path} !!!",
        )

    def start_sandbox(self) -> bool:
        logger.info("Локальная песочница 'запущена'.")
        return True

    def stop_sandbox(self) -> bool:
        logger.info("Локальная песочница 'остановлена'. Файлы не удаляются.")
        return True

    def execute_command(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        try:
            result = subprocess.run(
                command,
                check=False,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                cwd=self.project_path,
            )
        except subprocess.TimeoutExpired as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Таймаут: {e}",
                "exit_code": -1,
            }
        except OSError as e:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Ошибка: {e}",
                "exit_code": -1,
            }
        else:
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }

    def write_file_to_sandbox(self, content: str, filename: str) -> bool:
        try:
            file_path = self.project_path / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return True
        except OSError as e:
            logger.exception(f"Ошибка записи файла: {e}")
            return False
        else:
            return True

    def read_file_from_sandbox(self, filename: str) -> Optional[str]:
        try:
            file_path = self.project_path / filename
            return file_path.read_text(encoding="utf-8") if file_path.exists() else None
        except OSError as e:
            logger.exception(f"Ошибка чтения файла: {e}")
            return None
        else:
            return file_path.read_text(encoding="utf-8") if file_path.exists() else None

    def close(self):
        self.stop_sandbox()
