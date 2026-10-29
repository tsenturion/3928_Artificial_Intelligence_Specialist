"""Проверка и подготовка обращений с фиксированным разделением на выборки."""

from collections import Counter
import csv
import json
import logging
from pathlib import Path
import re
import unicodedata

from support_router import CATEGORIES
from support_router.config import Settings
from support_router.files import write_csv, write_json


LOGGER = logging.getLogger("support_router")
MAX_TEXT_LENGTH = 4000
PREPARED_COLUMNS = ["ticket_id", "text", "category", "group_id"]
RAW_COLUMNS = PREPARED_COLUMNS + ["split"]


def normalize_text(text: str) -> str:
    """Одинаково обрабатывает текст при подготовке данных и внутри сохранённого Pipeline."""
    if not isinstance(text, str):
        raise ValueError("Обращение должно быть строкой.")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"Обращение не должно превышать {MAX_TEXT_LENGTH} символов.")
    # NFKC выравнивает совместимые варианты символов; split/join убирает лишние пробелы.
    result = " ".join(unicodedata.normalize("NFKC", text).casefold().replace("ё", "е").split())
    if len(result) < 3 or not any(character.isalpha() for character in result):
        raise ValueError("Обращение должно содержать буквы и не менее трёх символов.")
    return result


def read_records(path: Path, *, raw: bool) -> list[dict]:
    """Читает CSV и отклоняет повреждённые строки, пустые поля и неизвестные категории."""
    columns = RAW_COLUMNS if raw else PREPARED_COLUMNS
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            raise ValueError(f"В {path.name} ожидаются столбцы в порядке: {', '.join(columns)}.")
        rows = []
        for line, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path.name}, строка {line}: неверное количество полей.")
            row = {key: value.strip() for key, value in row.items()}
            if any(not value for value in row.values()):
                raise ValueError(f"{path.name}, строка {line}: обнаружено пустое поле.")
            if row["category"] not in CATEGORIES:
                raise ValueError(f"{path.name}, строка {line}: неизвестная категория {row['category']!r}.")
            if raw and row["split"] not in ("train", "test"):
                raise ValueError(f"{path.name}, строка {line}: split должен быть train или test.")
            row["text"] = normalize_text(row["text"])
            rows.append(row)
    if not rows:
        raise ValueError(f"Файл {path.name} не содержит обращений.")
    return rows


def validate_partition(train: list[dict], test: list[dict]) -> None:
    """Проверяет состав категорий, дубликаты и пересечение связанных случаев."""
    ids, canonical_texts = set(), set()
    groups: dict[str, str] = {}
    for split, rows in (("train", train), ("test", test)):
        counts = Counter(row["category"] for row in rows)
        if set(counts) != set(CATEGORIES):
            raise ValueError(f"Выборка {split} должна содержать все четыре категории.")
        for row in rows:
            if row["ticket_id"] in ids:
                raise ValueError(f"Повторный ticket_id: {row['ticket_id']}.")
            ids.add(row["ticket_id"])
            # Разница только в пробелах, регистре и пунктуации не делает текст новым.
            canonical = re.sub(r"[\W_]+", "", normalize_text(row["text"]))
            if canonical in canonical_texts:
                raise ValueError(f"Повторный текст обращения: {row['ticket_id']}.")
            canonical_texts.add(canonical)
            group = row["group_id"]
            if group in groups and groups[group] != split:
                raise ValueError(f"Группа {group} попала одновременно в обучение и контроль.")
            groups[group] = split


def prepare(settings: Settings) -> dict:
    """Сохраняет две очищенные выборки; назначение split берётся из исходного CSV."""
    rows = read_records(settings.source, raw=True)
    # Включения списков выбирают строки по заранее сохранённому split.
    # Здесь нет случайного переразбиения, которое меняло бы контроль при каждом запуске.
    train = [row for row in rows if row["split"] == "train"]
    test = [row for row in rows if row["split"] == "test"]
    validate_partition(train, test)
    for split, partition in (("train", train), ("test", test)):
        prepared = [{key: row[key] for key in PREPARED_COLUMNS} for row in partition]
        write_csv(settings.processed_dir / f"{split}.csv", prepared, PREPARED_COLUMNS)
    summary = {
        "data_version": settings.data_version,
        "source": (
            settings.source.relative_to(settings.root).as_posix()
            if settings.source.is_relative_to(settings.root) else settings.source.as_posix()
        ),
        "train_rows": len(train),
        "test_rows": len(test),
        "train_categories": dict(Counter(row["category"] for row in train)),
        "test_categories": dict(Counter(row["category"] for row in test)),
    }
    write_json(settings.processed_dir / "manifest.json", summary)
    LOGGER.info("Данные подготовлены: обучение=%s, контроль=%s.", len(train), len(test))
    return summary


def load_partitions(settings: Settings) -> tuple[list[dict], list[dict]]:
    """Повторяет проверку при отдельном запуске обучения или оценки."""
    manifest = json.loads((settings.processed_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["data_version"] != settings.data_version:
        raise ValueError("Версия подготовленных данных изменилась. Сначала выполните prepare.")
    train = read_records(settings.processed_dir / "train.csv", raw=False)
    test = read_records(settings.processed_dir / "test.csv", raw=False)
    validate_partition(train, test)
    if len(train) != manifest["train_rows"] or len(test) != manifest["test_rows"]:
        raise ValueError("Размеры подготовленных выборок изменились. Повторите prepare.")
    return train, test
