"""Проверки наблюдаемости: сохранение, разметка, версии, срок хранения и сигналы."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest

from support_router import CATEGORIES, api
from support_router.config import load_settings
from support_router.data import prepare
from support_router.model import train
from support_router.monitoring import MonitoringStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def metadata(created_at="обучение-1"):
    return {
        "model_version": "0.1.0", "data_version": "1.0.0", "created_at": created_at,
        "monitoring_reference": {
            "length_mean": 40, "length_p05": 10, "length_p95": 100,
            "coverage_mean": 1.0,
            "category_shares": {name: 0.25 for name in CATEGORIES},
        },
    }


def details(category="delivery", length=40, coverage=0.95):
    return {"category": category, "probability": 0.8, "text_length": length, "coverage": coverage}


def test_store_survives_restart_and_counts_only_the_current_training(tmp_path):
    path = tmp_path / "monitoring.sqlite3"
    store = MonitoringStore(path)
    store.record("old", 200, 10, metadata("другое-обучение"), details())
    store.label("old", "delivery")
    for index, category in enumerate(CATEGORIES):
        store.record(str(index), 200, index + 1, metadata(), details(category))
        store.label(str(index), category)
    store.close()
    restored = MonitoringStore(path)
    try:
        report = restored.snapshot(metadata())
        assert report["traffic"]["requests"] == 5
        assert report["predictions"]["total"] == 4
        assert report["quality"] == {
            "labeled_count": 4, "accuracy": 1.0, "macro_f1": 1.0, "category_coverage": 4,
        }
    finally:
        restored.close()


def test_retention_removes_expired_events_and_labels(tmp_path):
    now = datetime.now(timezone.utc)
    store = MonitoringStore(tmp_path / "monitoring.sqlite3")
    try:
        store.record("expired", 200, 1, metadata(), details(), now=now - timedelta(days=31))
        store.record("recent", 200, 1, metadata(), details(), now=now - timedelta(days=2))
        report = store.snapshot(metadata(), hours=720, now=now)
        assert report["traffic"]["requests"] == 1
        assert store.snapshot(metadata(), hours=24, now=now)["traffic"]["requests"] == 0
        with pytest.raises(KeyError):
            store.label("expired", "delivery")
    finally:
        store.close()


def test_drift_requires_a_reference_and_enough_samples(tmp_path):
    store = MonitoringStore(tmp_path / "monitoring.sqlite3")
    try:
        assert store.snapshot(None)["data"]["status"] == "no_reference"
        assert store.snapshot(metadata())["quality"]["macro_f1"] is None
        for index in range(19):
            store.record(str(index), 200, 1, metadata(), details(length=1000, coverage=0.1))
        assert store.snapshot(metadata())["data"]["status"] == "insufficient_data"
        store.record("20", 200, 1, metadata(), details(length=1000, coverage=0.1))
        report = store.snapshot(metadata())
        assert report["data"]["status"] == "warning"
        assert len(report["data"]["warnings"]) == 2
        # Изменение входов не превращается в выдуманную оценку точности.
        assert report["quality"]["labeled_count"] == 0
        assert report["quality"]["accuracy"] is None
    finally:
        store.close()


def test_matching_input_profile_does_not_raise_a_warning(tmp_path):
    store = MonitoringStore(tmp_path / "monitoring.sqlite3")
    try:
        for index in range(20):
            store.record(str(index), 200, 1, metadata(), details())
        assert store.snapshot(metadata())["data"]["status"] == "ok"
    finally:
        store.close()


def test_parallel_records_are_not_lost(tmp_path):
    store = MonitoringStore(tmp_path / "monitoring.sqlite3")
    try:
        def write(index):
            store.record(str(index), 200, 1, metadata(), details())
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(40)))
        assert store.snapshot(metadata())["traffic"]["requests"] == 40
    finally:
        store.close()


@pytest.fixture(scope="module")
def trained_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("monitoring-model")
    (root / "data/raw").mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / "params.toml", root / "params.toml")
    shutil.copyfile(PROJECT_ROOT / "data/raw/tickets.csv", root / "data/raw/tickets.csv")
    settings = load_settings(root / "params.toml")
    prepare(settings)
    train(settings)
    return root


@pytest.fixture
def service_config(tmp_path, trained_root):
    (tmp_path / "models").mkdir()
    shutil.copyfile(trained_root / "params.toml", tmp_path / "params.toml")
    shutil.copyfile(trained_root / "models/model.joblib", tmp_path / "models/model.joblib")
    return tmp_path / "params.toml"


def test_api_counts_success_validation_failure_and_server_error(service_config, monkeypatch):
    with TestClient(api.create_app(service_config)) as client:
        for _ in range(2):
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/monitoring").json()["traffic"]["requests"] == 0
        prediction = client.post("/api/predict", json={"text": "Не пришла посылка"})
        assert prediction.json()["request_id"] == prediction.headers["X-Request-ID"]
        assert client.post("/api/predict", json={"text": "!!!"}).status_code == 422
        def fail(*args):
            raise RuntimeError("Проверка ошибки мониторинга")
        monkeypatch.setattr(api, "predict", fail)
        assert client.post("/api/predict", json={"text": "Не пришла посылка"}).status_code == 500
        report = client.get("/api/monitoring").json()
        assert report["traffic"]["requests"] == 3
        assert report["traffic"]["successful"] == 1
        assert report["traffic"]["client_errors"] == 1
        assert report["traffic"]["server_errors"] == 1
        assert report["traffic"]["error_rate"] == pytest.approx(2 / 3)
        assert report["traffic"]["p95_latency_ms"] >= 0
        assert report["predictions"]["total"] == 1


def test_feedback_updates_one_label_and_is_kept_after_restart(service_config):
    with TestClient(api.create_app(service_config)) as client:
        prediction = client.post("/api/predict", json={"text": "Курьер не привёз заказ"}).json()
        body = {"request_id": prediction["request_id"], "expected_category": prediction["category"]}
        assert client.post("/api/feedback", json=body).status_code == 200
        assert client.post("/api/feedback", json=body).status_code == 200
        assert client.get("/api/monitoring").json()["quality"]["labeled_count"] == 1
    with TestClient(api.create_app(service_config)) as client:
        report = client.get("/api/monitoring").json()
        assert report["traffic"]["requests"] == 1
        assert report["quality"]["accuracy"] == 1.0
        assert report["quality"]["category_coverage"] == 1


def test_feedback_cannot_label_unknown_or_failed_requests(service_config):
    with TestClient(api.create_app(service_config)) as client:
        failed = client.post("/api/predict", json={"text": "!!!"})
        for request_id in ("0" * 32, failed.headers["X-Request-ID"]):
            assert client.post("/api/feedback", json={
                "request_id": request_id, "expected_category": "delivery",
            }).status_code == 404
        assert client.post("/api/feedback", json={
            "request_id": "неверный-id", "expected_category": "delivery",
        }).status_code == 422


def test_monitoring_does_not_store_the_text(service_config):
    text = "ПриватныйУникальныйТекстОбращения хочу вернуть товар"
    with TestClient(api.create_app(service_config)) as client:
        assert client.post("/api/predict", json={"text": text}).status_code == 200
        assert client.get("/api/monitoring").json()["data"]["sample_count"] == 1
    database = service_config.parent / "logs/monitoring.sqlite3"
    assert text.encode("utf-8") not in database.read_bytes()


def test_storage_failure_does_not_break_classification(service_config, monkeypatch):
    app = api.create_app(service_config)
    with TestClient(app) as client:
        def fail(*args, **kwargs):
            raise OSError("Проверка недоступного диска")
        monkeypatch.setattr(app.state.monitoring, "record", fail)
        response = client.post("/api/predict", json={"text": "Курьер не привёз заказ"})
        assert response.status_code == 200
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert client.get("/api/monitoring").json()["traffic"]["requests"] == 0
    assert "Не удалось сохранить метрику запроса" in (
        service_config.parent / "logs/support-router.log"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("hours", [0, 721, "не-число"])
def test_monitoring_rejects_invalid_windows(service_config, hours):
    with TestClient(api.create_app(service_config)) as client:
        assert client.get("/api/monitoring", params={"hours": hours}).status_code == 422


def test_unavailable_model_is_included_in_operational_errors(tmp_path):
    config = tmp_path / "params.toml"
    shutil.copyfile(PROJECT_ROOT / "params.toml", config)
    with TestClient(api.create_app(config)) as client:
        assert client.post("/api/predict", json={"text": "Курьер задержался"}).status_code == 503
        report = client.get("/api/monitoring").json()
        assert report["traffic"]["server_errors"] == 1
        assert report["traffic"]["error_rate"] == 1.0
        assert report["model"] is None
