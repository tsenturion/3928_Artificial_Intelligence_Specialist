"""Проверка готового комплекта перед упаковкой в контейнер."""

import json
import logging

from support_router.config import Settings
from support_router.evaluate import quality_gate
from support_router.model import load_model


LOGGER = logging.getLogger("support_router")


def verify_release(settings: Settings) -> dict:
    """Не обучает модель и не меняет файлы; отклоняет чужой или неуспешный отчёт."""
    artifact = load_model(settings.model_path)
    metadata = artifact["metadata"]
    report_path = settings.reports_dir / "metrics.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for key in ("model_version", "data_version"):
        if report[key] != metadata[key]:
            raise ValueError(f"Отчёт и модель имеют разные значения {key}. Выполните dvc repro.")
    if (
        metadata["model_version"] != settings.model_version
        or metadata["data_version"] != settings.data_version
    ):
        raise ValueError("Настройки не соответствуют подготовленной модели. Выполните dvc repro.")
    if report.get("model_created_at") != metadata["created_at"]:
        raise ValueError("Отчёт получен для другого обучения. Выполните dvc repro.")
    # Перепроверяем числа, а не только записанный флаг успеха.
    if report["quality_gate"]["passed"] is not True or not quality_gate(
        report["macro_f1"], report["baseline_macro_f1"], settings.min_macro_f1
    ):
        raise ValueError("Модель не прошла текущий порог качества; выпуск остановлен.")
    LOGGER.info(
        "Комплект проверен: модель=%s, данные=%s, macro-F1=%.3f.",
        metadata["model_version"], metadata["data_version"], report["macro_f1"],
    )
    return {
        "model_version": metadata["model_version"],
        "data_version": metadata["data_version"],
        "macro_f1": report["macro_f1"],
    }
