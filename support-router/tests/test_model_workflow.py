"""Проверки ограничены основой модели: данные, отсутствие утечки, артефакт и команды."""

import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from sklearn.pipeline import Pipeline

from support_router import CATEGORIES
from support_router.cli import main
from support_router.config import load_settings
from support_router.data import RAW_COLUMNS, load_partitions, normalize_text, prepare
from support_router.evaluate import evaluate, quality_gate
from support_router.logging_utils import configure_logging
from support_router.model import load_model, predict, train


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path):
    """Каждая проверка работает в своей папке и не меняет артефакты проекта."""
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / "data" / "raw" / "tickets.csv", raw / "tickets.csv")
    shutil.copyfile(PROJECT_ROOT / "params.toml", tmp_path / "params.toml")
    return load_settings(tmp_path / "params.toml")


def rewrite_raw(path, rows):
    """Изменяет только копию исходных данных для проверок повреждённого ввода."""
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RAW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def raw_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_normalization_is_shared_and_deterministic():
    assert normalize_text("  НЕ   ПРИШЁЛ\nКУРЬЕР  ") == "не пришел курьер"


@pytest.mark.parametrize("text", ["", "  ", "!!!", "12", "а" * 4001, None])
def test_invalid_request_is_rejected(text):
    with pytest.raises(ValueError):
        normalize_text(text)


@pytest.mark.parametrize("damage", ["duplicate_id", "duplicate_text", "group_leak", "label", "split"])
def test_prepare_rejects_invalid_or_leaking_data(settings, damage):
    rows = raw_rows(settings.source)
    first_train = next(row for row in rows if row["split"] == "train")
    first_test = next(row for row in rows if row["split"] == "test")
    if damage == "duplicate_id":
        first_test["ticket_id"] = first_train["ticket_id"]
    elif damage == "duplicate_text":
        # Пунктуация и регистр не должны скрыть пересечение выборок.
        first_test["text"] = first_train["text"].upper() + "!!!"
    elif damage == "group_leak":
        first_test["group_id"] = first_train["group_id"]
    elif damage == "label":
        first_test["category"] = "неизвестная"
    else:
        first_test["split"] = "validation"
    rewrite_raw(settings.source, rows)
    with pytest.raises(ValueError):
        prepare(settings)
    assert not settings.processed_dir.exists()


def test_prepare_preserves_the_fixed_split_and_requires_all_categories(settings):
    summary = prepare(settings)
    assert summary["train_rows"] == 192 and summary["test_rows"] == 64
    train_rows, test_rows = load_partitions(settings)
    assert {row["ticket_id"] for row in train_rows}.isdisjoint(row["ticket_id"] for row in test_rows)
    assert {row["group_id"] for row in train_rows}.isdisjoint(row["group_id"] for row in test_rows)
    rows = [row for row in raw_rows(settings.source) if not (
        row["category"] == "technical" and row["split"] == "test"
    )]
    rewrite_raw(settings.source, rows)
    with pytest.raises(ValueError, match="четыре категории"):
        prepare(settings)


def test_training_requires_matching_prepared_data_version(settings):
    prepare(settings)
    with pytest.raises(ValueError, match="Сначала выполните prepare"):
        train(replace(settings, data_version="2.0.0"))


def test_pipeline_uses_only_training_vocabulary_and_restores_in_a_new_process(settings):
    rows = raw_rows(settings.source)
    for row in rows:
        if row["split"] == "test":
            row["text"] += " щщщ"
    rewrite_raw(settings.source, rows)
    prepare(settings)
    train(settings)
    artifact = load_model(settings.model_path)
    assert isinstance(artifact["pipeline"], Pipeline)
    assert "щщщ" not in artifact["pipeline"].named_steps["tfidf"].vocabulary_
    text = "  КУРЬЕР не привёз мой заказ "
    expected = predict(artifact, text)
    assert expected == predict(artifact, normalize_text(text))
    assert expected["category"] == "delivery"
    assert sum(expected["probabilities"].values()) == pytest.approx(1.0)
    assert expected["probability"] == max(expected["probabilities"].values())
    assert expected["category_name"] == CATEGORIES[expected["category"]]
    # Новый интерпретатор подтверждает, что в артефакте нет функций из временного __main__.
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "support_router",
         "--config", str(settings.root / "params.toml"), "predict", text],
        cwd=settings.root, env=environment, capture_output=True, text=True, encoding="utf-8",
        check=True,
    )
    assert json.loads(completed.stdout) == expected


def test_evaluation_reports_are_consistent_and_do_not_refit(settings, monkeypatch):
    prepare(settings)
    train(settings)
    artifact = load_model(settings.model_path)
    before = predict(artifact, "Деньги списали два раза")
    def forbid_fitting(*args, **kwargs):
        raise AssertionError("Оценка не должна переобучать Pipeline на контрольных данных.")
    monkeypatch.setattr(Pipeline, "fit", forbid_fitting)
    metrics = evaluate(settings)
    assert metrics["test_rows"] == 64
    assert metrics["quality_gate"]["passed"]
    matrix = metrics["confusion_matrix"]["values"]
    assert sum(sum(row) for row in matrix) == 64
    correct = sum(matrix[index][index] for index in range(4))
    with (settings.reports_dir / "errors.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 64 - correct
    assert predict(load_model(settings.model_path), "Деньги списали два раза") == before


@pytest.mark.parametrize(
    ("score", "baseline", "threshold", "expected"),
    [(0.1, 0.2, 0.0, False), (0.7, 0.1, 0.75, False), (0.75, 0.1, 0.75, True)],
)
def test_quality_gate_blocks_a_weak_release(score, baseline, threshold, expected):
    assert quality_gate(score, baseline, threshold) is expected


def test_cli_returns_a_failure_code_and_logs_the_diagnostic(settings, capsys):
    config = settings.root / "params.toml"
    assert main(["--config", str(config), "run"]) == 0
    capsys.readouterr()
    assert main(["--config", str(config), "predict", "!!!"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    log_text = (settings.root / "logs" / "support-router.log").read_text(encoding="utf-8")
    assert "ValueError" in log_text and "завершилась с ошибкой" in log_text
    config.write_text(config.read_text(encoding="utf-8").replace(
        "min_macro_f1 = 0.75", "min_macro_f1 = 1.0"
    ), encoding="utf-8")
    assert main(["--config", str(config), "evaluate"]) == 2


def test_log_retention_removes_only_owned_files_older_than_30_days(tmp_path):
    now = datetime.now(timezone.utc)
    old = tmp_path / ("support-router.log." + (now - timedelta(days=31)).strftime("%Y-%m-%d"))
    recent = tmp_path / ("support-router.log." + (now - timedelta(days=2)).strftime("%Y-%m-%d"))
    unrelated = tmp_path / "another-application.log"
    for path, age in ((old, 31), (recent, 2), (unrelated, 31)):
        path.write_text("Проверка хранения", encoding="utf-8")
        timestamp = (now - timedelta(days=age)).timestamp()
        os.utime(path, (timestamp, timestamp))
    logger = configure_logging(tmp_path)
    logger.info("Проверка записи журнала.")
    assert not old.exists() and recent.exists() and unrelated.exists()
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def close_log_files_after_test():
    yield
    # Windows должен освободить файлы до очистки временной папки pytest.
    logger = logging.getLogger("support_router")
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
