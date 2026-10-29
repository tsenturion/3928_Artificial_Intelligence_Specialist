"""Числовой профиль текста для сопоставления обучающих и входящих обращений.

Это наблюдаемые характеристики входа, а не признаки для нового классификатора.
Сравнение использует уже обученный TF-IDF и никогда не расширяет его словарь.
"""

from collections import Counter

import numpy as np

from support_router import CATEGORIES
from support_router.data import normalize_text


def text_features(pipeline, text: str) -> dict:
    """Возвращает длину нормализованного текста и покрытие словаря от 0 до 1.

    pipeline должен быть обучен. Анализатор выделяет те же фрагменты, которые
    использует модель; считаются их вхождения, включая повторяющиеся.
    Покрытие показывает знакомость фрагментов, а не понимание темы текста.
    """
    text = normalize_text(text)
    vectorizer = pipeline.named_steps["tfidf"]
    # build_analyzer возвращает настроенную функцию разбора; вторые скобки
    # применяют её к тексту. fit/fit_transform здесь вызывать нельзя.
    fragments = vectorizer.build_analyzer()(text)
    # Проверка принадлежности даёт True/False; sum подсчитывает True как единицы.
    known = sum(fragment in vectorizer.vocabulary_ for fragment in fragments)
    return {
        "text_length": len(text),
        "coverage": known / len(fragments) if fragments else 0.0,
    }


def build_reference(pipeline, texts: list[str], targets: list[str]) -> dict:
    """Строит JSON-совместимые ориентиры только по обучающим текстам и меткам.

    Длины и покрытие используются для простых сигналов изменения входов,
    category_shares — для сравнения долей категорий. Значения сохраняются
    внутри артефакта, поэтому исходные тексты в рабочем контейнере не нужны.
    """
    features = [text_features(pipeline, text) for text in texts]
    lengths = [item["text_length"] for item in features]
    counts = Counter(targets)
    # NumPy возвращает собственные числовые типы; float переводит их в Python
    # для одинакового сохранения в метаданных и последующей выдачи через API.
    return {
        "sample_count": len(texts),
        "length_mean": float(np.mean(lengths)),
        "length_p05": float(np.quantile(lengths, 0.05)),
        "length_p95": float(np.quantile(lengths, 0.95)),
        "coverage_mean": float(np.mean([item["coverage"] for item in features])),
        "category_shares": {category: counts[category] / len(texts) for category in CATEGORIES},
    }
