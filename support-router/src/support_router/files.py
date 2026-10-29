"""Запись небольших результатов через временный файл и замену целевого.

Читатель должен увидеть прежний или полностью записанный новый файл. Это
относится к одному файлу; согласованность модели и нескольких отчётов отдельно
проверяет release.py перед выпуском.
"""

import csv
import json
from pathlib import Path
import tempfile


def temporary_path(destination: Path) -> Path:
    """Возвращает путь созданного и уже закрытого временного файла.

    Каталог назначения создаётся при необходимости. Размещение рядом оставляет
    будущую замену в пределах одной файловой системы, без промежуточного
    копирования между дисками. За очистку отвечает вызывающая функция.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Файл закрывается до использования: это важно при работе в Windows.
    # delete=False оставляет его после выхода из with; удаление выполнит finally.
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
        return Path(stream.name)


def write_json(path: Path, content: dict) -> None:
    """Сначала записывает весь JSON во временный файл, затем заменяет результат."""
    temporary = temporary_path(path)
    try:
        # ensure_ascii=False сохраняет читаемый русский текст, allow_nan=False
        # отвергает NaN/Infinity, не являющиеся обычными JSON-числами.
        temporary.write_text(
            json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    # finally выполняется и при ошибке; после успешного replace временного имени
    # уже нет, поэтому missing_ok=True делает очистку безопасной.
    finally:
        temporary.unlink(missing_ok=True)


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    """Сохраняет UTF-8 CSV с заголовком; newline исключает пустые строки в Windows."""
    temporary = temporary_path(path)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            # columns задаёт стабильный порядок заголовков независимо от того,
            # как был собран отдельный словарь строки.
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
