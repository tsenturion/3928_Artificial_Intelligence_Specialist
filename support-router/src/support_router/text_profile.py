"""Числовой профиль текста для сопоставления обучающих и входящих обращений."""

from collections import Counter

import numpy as np

from support_router import CATEGORIES
from support_router.data import normalize_text


def text_features(pipeline, text: str) -> dict:
    """Длина нормализованного текста и доля известных символьных фрагментов TF-IDF."""
    text = normalize_text(text)
    vectorizer = pipeline.named_steps["tfidf"]
    fragments = vectorizer.build_analyzer()(text)
    known = sum(fragment in vectorizer.vocabulary_ for fragment in fragments)
    return {
        "text_length": len(text),
        "coverage": known / len(fragments) if fragments else 0.0,
    }


def build_reference(pipeline, texts: list[str], targets: list[str]) -> dict:
    """Сохраняет ориентиры из train в модели: при запуске API исходный CSV не нужен."""
    features = [text_features(pipeline, text) for text in texts]
    lengths = [item["text_length"] for item in features]
    counts = Counter(targets)
    return {
        "sample_count": len(texts),
        "length_mean": float(np.mean(lengths)),
        "length_p05": float(np.quantile(lengths, 0.05)),
        "length_p95": float(np.quantile(lengths, 0.95)),
        "coverage_mean": float(np.mean([item["coverage"] for item in features])),
        "category_shares": {category: counts[category] / len(texts) for category in CATEGORIES},
    }
