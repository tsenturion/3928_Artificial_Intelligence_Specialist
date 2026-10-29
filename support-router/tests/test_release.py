"""Проверка принадлежности отчёта модели и блокировки некачественного релиза."""

from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from support_router.config import load_settings
from support_router.data import prepare
from support_router.evaluate import evaluate
from support_router.model import train
from support_router.release import verify_release


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release_settings(tmp_path):
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / "params.toml", tmp_path / "params.toml")
    shutil.copyfile(PROJECT_ROOT / "data" / "raw" / "tickets.csv", raw / "tickets.csv")
    settings = load_settings(tmp_path / "params.toml")
    prepare(settings)
    train(settings)
    evaluate(settings)
    return settings


def test_verified_release_has_the_same_quality_as_the_numeric_dvc_report(release_settings):
    verified = verify_release(release_settings)
    scores = json.loads(
        (release_settings.reports_dir / "scores.json").read_text(encoding="utf-8")
    )
    assert verified["macro_f1"] == scores["macro_f1"]
    assert scores["quality_gate_passed"] == 1
    assert all(isinstance(value, (int, float)) for value in scores.values())


@pytest.mark.parametrize("field", ["model_version", "data_version", "model_created_at"])
def test_report_from_another_training_cannot_be_released(release_settings, field):
    report_path = release_settings.reports_dir / "metrics.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report[field] = "другая-версия"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_release(release_settings)


def test_release_rechecks_the_current_threshold(release_settings):
    with pytest.raises(ValueError, match="порог"):
        verify_release(replace(release_settings, min_macro_f1=1.0))


def test_a_false_success_flag_does_not_override_bad_metrics(release_settings):
    report_path = release_settings.reports_dir / "metrics.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["macro_f1"] = 0.01
    report["quality_gate"]["passed"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="порог"):
        verify_release(release_settings)
