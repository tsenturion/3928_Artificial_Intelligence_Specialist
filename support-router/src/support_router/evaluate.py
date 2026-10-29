"""Оценка сохранённой модели на фиксированной контрольной выборке.

Здесь сравнивается модель с простым baseline и формируется решение quality_gate.
Предсказания и ошибки сохраняются для разбора, а не для дообучения на test.
"""

from collections import Counter
from datetime import datetime, timezone
import logging

from sklearn.dummy import DummyClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

from support_router import CATEGORIES
from support_router.config import Settings
from support_router.data import load_partitions
from support_router.files import write_csv, write_json
from support_router.model import load_model


LOGGER = logging.getLogger("support_router")


def quality_gate(macro_f1: float, baseline_f1: float, threshold: float) -> bool:
    """Возвращает допуск выпуска: достигнут порог и превзойдён baseline.

    macro-F1 учитывает каждую категорию равноправно при неравном числе примеров.
    Одного превышения baseline недостаточно: нужен заданный минимум.
    И наоборот, проход по порогу не должен скрывать результат хуже baseline.
    """
    return macro_f1 >= threshold and macro_f1 > baseline_f1


def evaluate(settings: Settings) -> dict:
    """Оценивает сохранённый Pipeline на фиксированном test и возвращает отчёт.

    Пишет метрики и отдельные прогнозы/ошибки; контрольная выборка служит
    итоговой проверке, а не поиску лучшего набора гиперпараметров.
    fit вызывается только для отдельного DummyClassifier на train.
    Непройденный порог тоже записывается для диагностики; cli вернёт код 2.
    """
    train_rows, test_rows = load_partitions(settings)
    artifact = load_model(settings.model_path)
    metadata = artifact["metadata"]
    if metadata["data_version"] != settings.data_version:
        raise ValueError("Версия данных в настройках отличается от версии обученной модели.")
    if metadata["model_version"] != settings.model_version:
        raise ValueError("Версия модели в настройках отличается от загруженного артефакта.")
    texts = [row["text"] for row in test_rows]
    expected = [row["category"] for row in test_rows]
    pipeline = artifact["pipeline"]
    predicted = pipeline.predict(texts)
    # Для каждого обращения сохраняется оценка вероятности выбранного класса;
    # порядок столбцов задаёт сам обученный классификатор.
    # axis=1 выбирает максимум среди категорий для каждого обращения.
    probabilities = pipeline.predict_proba(texts).max(axis=1)
    labels = list(CATEGORIES)
    # Базовая модель всегда отвечает наиболее частой категорией из train.
    # Её частота определяется только train, иначе контроль влиял бы на эталон.
    baseline = DummyClassifier(strategy="most_frequent")
    baseline.fit([row["text"] for row in train_rows], [row["category"] for row in train_rows])
    baseline_predictions = baseline.predict(texts)
    # Общий список labels фиксирует четыре категории во всём отчёте.
    # zero_division=0 задаёт результат для неопределимой оценки отдельного класса.
    macro_f1 = float(f1_score(expected, predicted, labels=labels, average="macro", zero_division=0))
    baseline_f1 = float(f1_score(
        expected, baseline_predictions, labels=labels, average="macro", zero_division=0
    ))
    passed = quality_gate(macro_f1, baseline_f1, settings.min_macro_f1)
    metrics = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": metadata["model_version"],
        "data_version": metadata["data_version"],
        # Значение из артефакта связывает отчёт именно с этим обучением,
        # даже если человек пока не изменил читаемую версию модели.
        "model_created_at": metadata["created_at"],
        "test_rows": len(test_rows),
        "test_categories": dict(Counter(expected)),
        "accuracy": float(accuracy_score(expected, predicted)),
        "macro_f1": macro_f1,
        "baseline_accuracy": float(accuracy_score(expected, baseline_predictions)),
        "baseline_macro_f1": baseline_f1,
        # Порог записан рядом с результатом: изменение params.toml после оценки
        # требует новой проверки перед выпуском.
        "quality_gate": {"min_macro_f1": settings.min_macro_f1, "passed": passed},
        "category_names": CATEGORIES,
        "classification_report": classification_report(
            expected, predicted, labels=labels, output_dict=True, zero_division=0
        ),
        # В матрице строки — правильные категории, столбцы — ответы модели;
        # labels сохраняет смысл индексов после превращения массива в JSON.
        "confusion_matrix": {"labels": labels, "values": confusion_matrix(
            expected, predicted, labels=labels
        ).tolist()},
        "limitation": "Контроль синтетический; результат не оценивает качество на реальных клиентах.",
    }
    # Эти тексты относятся к синтетическому контрольному CSV.
    # Рабочий мониторинг пользовательских запросов тексты не записывает.
    details = [
        {
            "ticket_id": row["ticket_id"], "text": row["text"],
            "expected": row["category"], "predicted": str(category),
            "probability": float(probability),
        }
        for row, category, probability in zip(test_rows, predicted, probabilities)
    ]
    columns = ["ticket_id", "text", "expected", "predicted", "probability"]
    write_csv(settings.reports_dir / "predictions.csv", details, columns)
    write_csv(settings.reports_dir / "errors.csv", [
        row for row in details if row["expected"] != row["predicted"]
    ], columns)
    write_json(settings.reports_dir / "metrics.json", metrics)
    # Для dvc metrics show сохраняем отдельный плоский набор чисел.
    # Подробный отчёт выше содержит ещё названия, даты и матрицу ошибок.
    write_json(settings.reports_dir / "scores.json", {
        "accuracy": metrics["accuracy"],
        "macro_f1": macro_f1,
        "baseline_macro_f1": baseline_f1,
        "test_rows": len(test_rows),
        "min_macro_f1": settings.min_macro_f1,
        "quality_gate_passed": int(passed),
    })
    LOGGER.info(
        "Оценка завершена: accuracy=%.3f, macro-F1=%.3f, базовая macro-F1=%.3f, проверка=%s.",
        metrics["accuracy"], macro_f1, baseline_f1, "пройдена" if passed else "не пройдена",
    )
    return metrics
