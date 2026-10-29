"""Обучение, сохранение и использование единого текстового Pipeline."""

from collections import Counter
from datetime import datetime, timezone
import logging
from pathlib import Path
import platform
import warnings

import joblib
import numpy as np
import scipy
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_is_fitted

from support_router import CATEGORIES, __version__
from support_router.config import Settings
from support_router.data import load_partitions, normalize_text
from support_router.files import temporary_path
from support_router.text_profile import build_reference


LOGGER = logging.getLogger("support_router")


def train(settings: Settings) -> dict:
    """Обучает TF-IDF и классификатор только на обучающей части, затем сохраняет комплект."""
    train_rows, _ = load_partitions(settings)
    texts = [row["text"] for row in train_rows]
    targets = [row["category"] for row in train_rows]
    # Pipeline связывает превращение строк в признаки с классификатором.
    # Сохранять только LogisticRegression недостаточно: без обученного TF-IDF
    # новые тексты не будут представлены теми же признаками.
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            analyzer="char_wb", ngram_range=settings.ngram_range,
            preprocessor=normalize_text, lowercase=False, sublinear_tf=True,
        )),
        ("classifier", LogisticRegression(
            C=settings.regularization_c, max_iter=settings.max_iter,
            random_state=settings.random_state, class_weight="balanced", solver="lbfgs",
        )),
    ])
    LOGGER.info("Обучение начато: строк=%s, версия модели=%s.", len(texts), settings.model_version)
    # Словарь TF-IDF строится внутри fit исключительно по train.
    # Недостаточное число итераций считается ошибкой, а не успешным выпуском модели.
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        pipeline.fit(texts, targets)
    metadata = {
        "model_version": settings.model_version,
        "data_version": settings.data_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "package_version": __version__,
        "python_version": platform.python_version(),
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "joblib_version": joblib.__version__,
        "categories": CATEGORIES,
        "train_rows": len(train_rows),
        "train_categories": dict(Counter(targets)),
        "parameters": {
            "analyzer": "char_wb", "ngram_range": list(settings.ngram_range),
            "C": settings.regularization_c, "max_iter": settings.max_iter,
            "random_state": settings.random_state,
        },
        "features": len(pipeline.named_steps["tfidf"].vocabulary_),
        "monitoring_reference": build_reference(pipeline, texts, targets),
        "data_origin": "Синтетические обращения; не данные реальных клиентов.",
    }
    artifact = {"format_version": 1, "pipeline": pipeline, "metadata": metadata}
    # Модель и метаданные находятся в одном файле и заменяются вместе.
    temporary = temporary_path(settings.model_path)
    try:
        joblib.dump(artifact, temporary, compress=3)
        temporary.replace(settings.model_path)
    finally:
        temporary.unlink(missing_ok=True)
    LOGGER.info("Модель сохранена: %s; признаков=%s.", settings.model_path, metadata["features"])
    return metadata


def load_model(path: Path) -> dict:
    """Загружает собственный локальный артефакт и проверяет его формат и окружение."""
    # joblib использует механизм pickle: такой файл должен происходить из доверенного проекта.
    artifact = joblib.load(path)
    if not isinstance(artifact, dict) or artifact.get("format_version") != 1:
        raise ValueError("Неподдерживаемый формат файла модели.")
    pipeline, metadata = artifact["pipeline"], artifact["metadata"]
    if not isinstance(pipeline, Pipeline) or set(metadata["categories"]) != set(CATEGORIES):
        raise ValueError("Артефакт содержит неподходящий Pipeline или категории.")
    if metadata["sklearn_version"] != sklearn.__version__:
        raise ValueError(
            f"Модель сохранена в scikit-learn {metadata['sklearn_version']}, "
            f"а установлена версия {sklearn.__version__}. Используйте зависимости проекта."
        )
    check_is_fitted(pipeline.named_steps["classifier"])
    return artifact


def predict(artifact: dict, text: str) -> dict:
    """Возвращает категорию, вероятности в правильном порядке и версию модели."""
    text = normalize_text(text)
    pipeline = artifact["pipeline"]
    probabilities = pipeline.predict_proba([text])[0]
    # Порядок столбцов predict_proba определяется classes_, а не словарём названий.
    classes = pipeline.classes_.tolist()
    # argmax возвращает индекс наибольшей вероятности; по нему выбирается имя категории.
    best_index = int(probabilities.argmax())
    category = classes[best_index]
    return {
        "category": category,
        "category_name": CATEGORIES[category],
        # Это оценка классификатора; её нельзя считать гарантией правильного ответа.
        "probability": float(probabilities[best_index]),
        "probabilities": {name: float(value) for name, value in zip(classes, probabilities)},
        "model_version": artifact["metadata"]["model_version"],
        "data_version": artifact["metadata"]["data_version"],
    }
