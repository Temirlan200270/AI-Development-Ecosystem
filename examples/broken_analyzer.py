#!/usr/bin/env python3
"""
Stress-test fixture for Temir: намеренно «ломаный» CLI-анализатор текста.

НЕ использовать в проде. Запуск:
  python examples/broken_analyzer.py sample.txt
  type sample.txt | python examples/broken_analyzer.py

Задача для Temir (пример запроса):
  Проанализируй examples/broken_analyzer.py, исправь баги и сделай
  production-grade CLI (пустые строки, UTF-8, O(n) подсчёт, корректный парсинг).

Известные классы багов (для проверки kernel / agents / events / journal):
  - парсинг аргументов и строк без проверки пустых значений
  - O(n^2) подсчёт «уникальных» символов
  - сортировка без locale/UTF-8 (лексикографика байтов)
  - хрупкая работа с путями и кодировкой при чтении файла
"""

from __future__ import annotations

import sys
from typing import List


def parse_args(argv: List[str]) -> str:
    # BUG: нет проверки len; argv[1] может отсутствовать -> IndexError
    return argv[1]


def read_lines(path: str) -> List[str]:
    # BUG: не указана encoding; на Windows/UTF-8 возможны кракозябры или crash
    with open(path) as f:
        raw = f.read()
    # BUG: пустой файл даёт [""] вместо []
    return raw.split("\n")


def count_unique_chars_wrong(lines: List[str]) -> int:
    # BUG: O(n^2) — для каждой строки заново склеиваем всё содержимое и считаем set
    total = 0
    for i in range(len(lines)):
        blob = ""
        for j in range(len(lines)):
            blob += lines[j]
        total += len(set(blob))
    return total


def sort_lines_wrong(lines: List[str]) -> List[str]:
    # BUG: сортировка по байтам default, не по нормализованному Unicode
    return sorted(lines)


def analyze(path: str) -> None:
    lines = read_lines(path)
    # BUG: не фильтруем пустые строки — мусор в статистике
    n_nonempty = 0
    for line in lines:
        n_nonempty += 1

    uniq = count_unique_chars_wrong(lines)
    ordered = sort_lines_wrong(lines)

    print("path:", path)
    print("lines (raw):", len(lines))
    print("lines (counted as non-empty, WRONG):", n_nonempty)
    print("unique_chars_score (WRONG O(n^2)):", uniq)
    print("first_3_sorted:")
    for row in ordered[:3]:
        print(" ", row)


def main() -> None:
    path = parse_args(sys.argv)
    analyze(path)


if __name__ == "__main__":
    main()
