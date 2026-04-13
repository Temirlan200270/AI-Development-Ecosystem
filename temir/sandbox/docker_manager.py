"""
Docker Sandbox Manager for Temir CLI.

Этот модуль управляет Docker-контейнерами для безопасного выполнения кода.
"""

try:
    import docker
except Exception:
    docker = None
import shutil
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

OUTPUT_TUPLE_LENGTH = 2

logger = logging.getLogger(__name__)


class DockerManager:
    """Управление Docker-контейнерами для безопасного выполнения кода."""

    def __init__(self):
        """Инициализация Docker клиента."""
        try:
            self.client = docker.from_env()
            self.container = None
            self.container_name = "temir-sandbox"
        except docker.errors.DockerException as e:
            logger.error(f"Не удалось подключиться к Docker: {e}")
            raise RuntimeError("Docker не запущен или недоступен")

    def start_sandbox(self, image: str = "python:3.11-slim") -> bool:
        """
        Запустить песочницу (Docker контейнер).

        Args:
            image: Docker образ для использования

        Returns:
            True если контейнер успешно запущен
        """
        try:
            # Остановить существующий контейнер если он есть
            self.stop_sandbox()

            # Создать временную директорию для монтирования
            self.temp_dir = tempfile.mkdtemp(prefix="temir_sandbox_")

            # Запустить контейнер (без remove=True — чтобы корректно контролировать lifecycle)
            self.container = self.client.containers.run(
                image=image,
                name=self.container_name,
                volumes={self.temp_dir: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                detach=True,
                tty=True,
                stdin_open=True,
                network_mode="none",  # Отключить сеть для безопасности
                mem_limit="512m",  # Ограничить память
                cpu_quota=50000,  # Ограничить CPU
            )

            logger.info(f"Песочница запущена: {self.container.id[:12]}")
            return True

        except Exception as e:
            logger.error(f"Ошибка при запуске песочницы: {e}")
            return False

    def stop_sandbox(self) -> bool:
        """
        Остановить песочницу (Docker контейнер).

        Returns:
            True если контейнер успешно остановлен
        """
        try:
            # Найти и остановить контейнер
            containers = self.client.containers.list(
                all=True,
                filters={"name": self.container_name},
            )

            for container in containers:
                if container.name == self.container_name:
                    try:
                        container.stop()
                        logger.info(f"Песочница остановлена: {container.id[:12]}")
                    except Exception:
                        logger.exception(
                            "Ошибка при остановке контейнера %s",
                            container.name,
                        )
                    try:
                        container.remove()
                        logger.info("Контейнер удалён: %s", container.name)
                    except Exception:
                        logger.exception(
                            "Ошибка при удалении контейнера %s",
                            container.name,
                        )

            # Очистить временную директорию
            if hasattr(self, "temp_dir") and self.temp_dir.exists():
                import shutil

                shutil.rmtree(self.temp_dir)

            self.container = None
            return True
        except docker.errors.APIError as e:
            logger.exception(f"Ошибка при остановке песочницы: {e}")
            return False

    def execute_command(self, command: str, timeout: int = 60) -> Dict[str, Any]:
        """
        Выполнить команду в песочнице.

        Args:
            command: Команда для выполнения
            timeout: Таймаут в секундах

        Returns:
            Dict с результатами выполнения:
                - success: bool - успешно ли выполнение
                - stdout: str - стандартный вывод
                - stderr: str - стандартный вывод ошибок
                - exit_code: int - код выхода
        """
        if not self.container:
            logger.error("Песочница не запущена")
            return {
                "success": False,
                "stdout": "",
                "stderr": "Песочница не запущена",
                "exit_code": -1,
            }

        try:
            exec_result = self.container.exec_run(cmd=["sh", "-c", command], demux=True, timeout=timeout)

            if isinstance(exec_result, tuple):
                exit_code, output = exec_result
            else:
                try:
                    exit_code = getattr(exec_result, "exit_code", None)
                    output = getattr(exec_result, "output", None)
                except docker.errors.APIError:
                    exit_code = None
                    output = None

            stdout = ""
            stderr = ""

            if isinstance(output, tuple) and len(output) == OUTPUT_TUPLE_LENGTH:
                out_bytes, err_bytes = output
                if out_bytes:
                    stdout = (
                        out_bytes.decode("utf-8", errors="replace")
                        if isinstance(out_bytes, (bytes, bytearray))
                        else str(out_bytes)
                    )
                if err_bytes:
                    stderr = (
                        err_bytes.decode("utf-8", errors="replace")
                        if isinstance(err_bytes, (bytes, bytearray))
                        else str(err_bytes)
                    )
            elif isinstance(output, (bytes, bytearray)):
                stdout = output.decode("utf-8", errors="replace")
            elif output is not None:
                stdout = str(output)

            success = (exit_code == 0) if exit_code is not None else (stderr == "")

            return {
                "success": bool(success),
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": int(exit_code) if exit_code is not None else -1,
            }

        except docker.errors.APIError as e:
            logger.exception(f"Ошибка при выполнении команды в песочнице: {e}")
            return {"success": False, "stdout": "", "stderr": str(e), "exit_code": -1}

    def write_file_to_sandbox(self, content: str, filename: str) -> bool:
        """
        Записать файл в песочницу.

        Args:
            content: Содержимое файла
            filename: Имя файла

        Returns:
            True если файл успешно записан
        """
        if not self.container:
            logger.error("Песочница не запущена")
            return False

        try:
            # Создать временный файл
            temp_file = Path(self.temp_dir) / filename
            temp_file.parent.mkdir(parents=True, exist_ok=True)

            # Записать содержимое
            temp_file.write_text(content, encoding="utf-8")

            logger.info(f"Файл записан в песочницу: {filename}")
            return True

        except OSError as e:
            logger.exception(f"Ошибка при записи файла в песочницу: {e}")
            return False
        else:
            logger.info(f"Файл записан в песочницу: {filename}")
            return True

    def read_file_from_sandbox(self, filename: str) -> Optional[str]:
        """
        Прочитать файл из песочницы.

        Args:
            filename: Имя файла

        Returns:
            Содержимое файла или None если ошибка
        """
        if not self.container:
            logger.error("Песочница не запущена")
            return None

        try:
            # Прочитать файл из временной директории
            temp_file = Path(self.temp_dir) / filename

            if temp_file.exists():
                return temp_file.read_text(encoding="utf-8")
            # Попробовать прочитать из контейнера
            result = self.execute_command(f"cat {filename}")
            if result["success"]:
                return result["stdout"]
            return None

        except OSError as e:
            logger.exception(f"Ошибка при чтении файла из песочницы: {e}")
            return None
        else:
            if temp_file.exists():
                return temp_file.read_text(encoding="utf-8")
            # Попробовать прочитать из контейнера
            result = self.execute_command(f"cat {filename}")
            if result["success"]:
                return result["stdout"]
            return None

    def close(self) -> None:
        """Явно закрыть менеджер и остановить песочницу."""
        try:
            self.stop_sandbox()
        except Exception:
            logger.exception("Error while closing DockerManager")

    def __enter__(self):
        # НЕ создаём контейнер автоматически здесь: вызывающий код сам решает, нужен ли старт
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


# NOTE: this module exposes DockerManager class only. Avoid global singletons
# and helper functions so that callers (main/orchestrator) can manage lifecycle
# via Dependency Injection.
