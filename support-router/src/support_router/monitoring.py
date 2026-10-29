"""Метрики сервиса и входных данных; тексты обращений в хранилище не записываются."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from threading import Lock

import numpy as np
from sklearn.metrics import accuracy_score, f1_score

from support_router import CATEGORIES


RETENTION_DAYS = 30
MIN_DRIFT_SAMPLES = 20
# Простые учебные эвристики, а не статистический тест или универсальные пороги.
MAX_LENGTH_OUTLIER_SHARE = 0.40
MAX_COVERAGE_DROP = 0.20


def _stamp(value: datetime) -> str:
    """Единый UTC-формат позволяет корректно сравнивать даты в SQLite."""
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class MonitoringStore:
    """Небольшое постоянное хранилище для одного процесса сервиса."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Обработчики FastAPI работают в разных потоках. Один Lock последовательно
        # выполняет операции с соединением; отдельный сервер БД здесь не требуется.
        self._lock = Lock()
        self._connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS prediction_events (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    model_version TEXT,
                    data_version TEXT,
                    model_created_at TEXT,
                    category TEXT,
                    probability REAL,
                    text_length INTEGER,
                    coverage REAL,
                    expected_category TEXT
                )
            """)
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS events_time ON prediction_events(created_at)"
            )
            self._prune(datetime.now(timezone.utc))

    def _prune(self, now: datetime) -> None:
        # Удаляются только записи этой таблицы старше 30 дней.
        self._connection.execute(
            "DELETE FROM prediction_events WHERE created_at < ?",
            (_stamp(now - timedelta(days=RETENTION_DAYS)),),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record(
        self, request_id: str, status_code: int, duration_ms: float,
        metadata: dict | None, details: dict | None = None, *, now: datetime | None = None,
    ) -> None:
        """Фиксирует один POST /api/predict, включая ошибки валидации и недоступность."""
        now = now or datetime.now(timezone.utc)
        metadata = metadata or {}
        details = details if status_code == 200 and details else {}
        with self._lock, self._connection:
            self._prune(now)
            # Значения передаются через ?, а не вставляются в SQL-строку.
            self._connection.execute("""
                INSERT INTO prediction_events
                (request_id, created_at, status_code, duration_ms, model_version,
                 data_version, model_created_at, category, probability, text_length, coverage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                request_id, _stamp(now), status_code, duration_ms,
                metadata.get("model_version"), metadata.get("data_version"), metadata.get("created_at"),
                details.get("category"), details.get("probability"),
                details.get("text_length"), details.get("coverage"),
            ))

    def label(self, request_id: str, expected_category: str) -> None:
        """Привязывает правильную категорию к успешному запросу, не дублируя запись."""
        if expected_category not in CATEGORIES:
            raise ValueError("Неизвестная категория разметки.")
        with self._lock, self._connection:
            self._prune(datetime.now(timezone.utc))
            cursor = self._connection.execute(
                """UPDATE prediction_events SET expected_category = ?
                   WHERE request_id = ? AND status_code = 200 AND category IS NOT NULL""",
                (expected_category, request_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("Успешный запрос не найден или срок его хранения истёк.")

    def snapshot(
        self, metadata: dict | None, hours: int = 24, *, now: datetime | None = None,
    ) -> dict:
        """API-метрики за окно; данные и качество только для текущего обучения."""
        if not 1 <= hours <= RETENTION_DAYS * 24:
            raise ValueError("Окно мониторинга должно быть от 1 до 720 часов.")
        now = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            self._prune(now)
            rows = [dict(row) for row in self._connection.execute(
                "SELECT * FROM prediction_events WHERE created_at >= ? AND created_at <= ?",
                (_stamp(now - timedelta(hours=hours)), _stamp(now)),
            ).fetchall()]
        # Операционная статистика охватывает все запросы классификации, в том числе
        # ошибки при отсутствии модели. Проверки health и чтение панели сюда не входят.
        durations = [row["duration_ms"] for row in rows]
        # Даже при прежнем номере версии повторное обучение создаёт новый экземпляр.
        # Дата обучения не позволяет смешать его прогнозы и разметку с предыдущими.
        current = [
            row for row in rows
            if metadata and row["status_code"] == 200 and row["category"] is not None
            and row["model_created_at"] == metadata["created_at"]
            and row["model_version"] == metadata["model_version"]
            and row["data_version"] == metadata["data_version"]
        ]
        reference = metadata.get("monitoring_reference") if metadata else None
        counts = Counter(row["category"] for row in current)
        labeled = [row for row in current if row["expected_category"] is not None]
        expected = [row["expected_category"] for row in labeled]
        predicted = [row["category"] for row in labeled]
        client_errors = sum(400 <= row["status_code"] < 500 for row in rows)
        server_errors = sum(row["status_code"] >= 500 for row in rows)
        return {
            "window_hours": hours,
            "generated_at": _stamp(now),
            "model": {
                "model_version": metadata["model_version"],
                "data_version": metadata["data_version"],
                "model_created_at": metadata["created_at"],
            } if metadata else None,
            "traffic": {
                "requests": len(rows),
                "successful": sum(row["status_code"] == 200 for row in rows),
                "client_errors": client_errors,
                "server_errors": server_errors,
                "error_rate": (client_errors + server_errors) / len(rows) if rows else 0.0,
                "avg_latency_ms": float(np.mean(durations)) if durations else None,
                # p95: 95-й процентиль измеренных длительностей обработки запроса.
                "p95_latency_ms": float(np.quantile(durations, 0.95)) if durations else None,
            },
            "predictions": {
                "total": len(current),
                "avg_probability": float(np.mean([row["probability"] for row in current])) if current else None,
                "categories": [{
                    "category": category, "name": name, "count": counts[category],
                    "share": counts[category] / len(current) if current else 0.0,
                    "reference_share": reference["category_shares"][category] if reference else None,
                } for category, name in CATEGORIES.items()],
            },
            "data": data_summary(current, reference),
            "quality": {
                "labeled_count": len(labeled),
                "accuracy": float(accuracy_score(expected, predicted)) if labeled else None,
                # Фиксируем четыре категории; отсутствующие в разметке получают F1=0.
                "macro_f1": float(f1_score(
                    expected, predicted, labels=list(CATEGORIES), average="macro", zero_division=0
                )) if labeled else None,
                "category_coverage": len(set(expected)),
            },
        }


def data_summary(rows: list[dict], reference: dict | None) -> dict:
    """Сигнал изменения характеристик входа; не оценка качества классификатора."""
    usable = [row for row in rows if row["text_length"] is not None and row["coverage"] is not None]
    lengths = [row["text_length"] for row in usable]
    coverages = [row["coverage"] for row in usable]
    outside_share = None
    if reference and usable:
        outside_share = sum(
            not reference["length_p05"] <= length <= reference["length_p95"] for length in lengths
        ) / len(usable)
    result = {
        "status": "no_reference", "sample_count": len(usable), "min_samples": MIN_DRIFT_SAMPLES,
        "avg_length": float(np.mean(lengths)) if usable else None,
        "reference_avg_length": reference["length_mean"] if reference else None,
        "avg_coverage": float(np.mean(coverages)) if usable else None,
        "reference_avg_coverage": reference["coverage_mean"] if reference else None,
        "length_outlier_share": outside_share,
        "warnings": [],
    }
    if not reference:
        return result
    if len(usable) < MIN_DRIFT_SAMPLES:
        result["status"] = "insufficient_data"
        return result
    if outside_share > MAX_LENGTH_OUTLIER_SHARE:
        result["warnings"].append("Много обращений с длиной вне обычного диапазона обучающей выборки.")
    if result["avg_coverage"] < reference["coverage_mean"] - MAX_COVERAGE_DROP:
        result["warnings"].append("Снизилась доля знакомых модели символьных фрагментов.")
    result["status"] = "warning" if result["warnings"] else "ok"
    return result
