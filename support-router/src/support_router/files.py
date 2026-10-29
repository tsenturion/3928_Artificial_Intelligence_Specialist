"""Запись небольших результатов без оставления частично записанных файлов."""

import csv
import json
from pathlib import Path
import tempfile


def temporary_path(destination: Path) -> Path:
    """Создаёт временный файл рядом с целевым, чтобы замена оставалась локальной."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Файл закрывается до использования: это важно при работе в Windows.
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
        return Path(stream.name)


def write_json(path: Path, content: dict) -> None:
    """Сначала записывает весь JSON во временный файл, затем заменяет результат."""
    temporary = temporary_path(path)
    try:
        temporary.write_text(
            json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    """Сохраняет UTF-8 CSV с заголовком; newline исключает пустые строки в Windows."""
    temporary = temporary_path(path)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
