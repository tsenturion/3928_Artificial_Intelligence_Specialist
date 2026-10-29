"""Обучение, сохранение и использование единого текстового Pipeline.

Один артефакт содержит обученный векторизатор, классификатор и метаданные.
И командная строка, и FastAPI вызывают один predict: правила преобразования
обращения не приходится воспроизводить отдельно в коде веб-сервиса.
"""

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
    """Обучает и сохраняет Pipeline по train, возвращает метаданные обучения.

    Контрольная часть загружается лишь для проверки разделения: ни словарь
    признаков, ни коэффициенты классификатора по ней не подстраиваются.
    Успешное обучение ещё не разрешает выпуск: оценку проводит evaluate.
    """
    train_rows, _ = load_partitions(settings)
    texts = [row["text"] for row in train_rows]
    targets = [row["category"] for row in train_rows]
    # Pipeline связывает превращение строк в признаки с классификатором.
    # Сохранять только LogisticRegression недостаточно: без обученного TF-IDF
    # новые тексты не будут представлены теми же признаками.
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            # char_wb строит символьные фрагменты внутри границ слов; тот же
            # normalize_text будет вызван и для новых обращений при inference.
            analyzer="char_wb", ngram_range=settings.ngram_range,
            preprocessor=normalize_text, lowercase=False, sublinear_tf=True,
        )),
        # C управляет силой регуляризации, max_iter ограничивает работу решателя.
        # balanced учитывает частоты классов при обучении; это не замена контроля
        # качества и не гарантия хорошей работы на редких категориях.
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
    # Версии библиотек и параметры помогают воспроизвести обучение и отклонить
    # несовместимый артефакт; created_at отличает два обучения с той же версией.
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
        # Профиль только train упаковывается вместе с моделью: рабочему контейнеру
        # не потребуется доступ к исходным обучающим обращениям.
        "monitoring_reference": build_reference(pipeline, texts, targets),
        "data_origin": "Синтетические обращения; не данные реальных клиентов.",
    }
    artifact = {"format_version": 1, "pipeline": pipeline, "metadata": metadata}
    # Модель и метаданные находятся в одном файле и заменяются вместе.
    # Сначала пишем рядом временный файл: читатель увидит либо прежний,
    # либо полностью записанный новый комплект.
    temporary = temporary_path(settings.model_path)
    try:
        # joblib сохраняет в том числе числовые массивы обученного sklearn Pipeline.
        # Умеренное сжатие удобно для небольшого файла этого проекта.
        joblib.dump(artifact, temporary, compress=3)
        temporary.replace(settings.model_path)
    finally:
        temporary.unlink(missing_ok=True)
    LOGGER.info("Модель сохранена: %s; признаков=%s.", settings.model_path, metadata["features"])
    return metadata


def load_model(path: Path) -> dict:
    """Возвращает доверенный локальный артефакт после проверки формата и окружения.

    Сохранённый Pipeline содержит обученный словарь TF-IDF и классификатор:
    отдельная подготовка текста при загрузке не нужна.
    Версия формата относится к нашему словарю, версия sklearn — к совместимости
    объектов библиотеки. check_is_fitted отвергает необученный классификатор.
    """
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
    """Возвращает прогноз и вероятности для строки по классам модели.

    Вход проверяется тем же нормализатором, что и учебные данные; словарь
    вероятностей строится по classes_, а не по предполагаемому порядку категорий.
    Результат содержит обычные Python-типы: числа NumPy явно приводятся к float,
    чтобы ответ можно было сериализовать в JSON.
    """
    text = normalize_text(text)
    pipeline = artifact["pipeline"]
    # sklearn принимает коллекцию документов даже для одного обращения.
    # [text] создаёт такую коллекцию, [0] выбирает её единственную строку ответа.
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
        # zip сопоставляет каждый столбец с соответствующим classes_.
        "probabilities": {name: float(value) for name, value in zip(classes, probabilities)},
        "model_version": artifact["metadata"]["model_version"],
        "data_version": artifact["metadata"]["data_version"],
    }
