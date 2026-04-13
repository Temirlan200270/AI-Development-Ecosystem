"""
Загрузка переменных из .env (не перезаписывает уже заданные в ОС).
Ищет файлы: каталог запуска (cwd), затем корень репозитория рядом с пакетом temir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple


def dotenv_candidate_paths() -> Tuple[Path, ...]:
    """Пути .env в порядке загрузки (cwd, затем корень репозитория). Файл может отсутствовать."""
    candidates: list[Path] = []
    candidates.append(Path.cwd() / ".env")

    pkg_dir = Path(__file__).resolve().parent
    repo_root = pkg_dir.parent
    if (repo_root / "temir").is_dir() and (repo_root / "pyproject.toml").is_file():
        candidates.append(repo_root / ".env")

    out: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return tuple(out)


def load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    seen: set[Path] = set()
    for path in dotenv_candidate_paths():
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            load_dotenv(path, override=False)
