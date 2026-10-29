"""Проверки жизненного цикла FastAPI, контракта HTTP и выдачи собранных ресурсов.

TestClient обращается к приложению внутри процесса, без отдельного Uvicorn.
Его контекст запускает lifespan, поэтому проверяется реальная загрузка модели.
Docker-сборку и соединение с работающим контейнером отдельно проверяет
scripts/check_service.py: эти два уровня проверки дополняют друг друга.
"""

from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest
from sklearn.pipeline import Pipeline

from support_router import CATEGORIES
from support_router import api
from support_router.config import load_settings
from support_router.data import prepare
from support_router.model import train


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def config_path(tmp_path_factory):
    """Модель для API-тестов готовится отдельно от пользовательского артефакта."""
    root = tmp_path_factory.mktemp("api-project")
    raw = root / "data" / "raw"
    raw.mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / "params.toml", root / "params.toml")
    shutil.copyfile(PROJECT_ROOT / "data" / "raw" / "tickets.csv", raw / "tickets.csv")
    settings = load_settings(root / "params.toml")
    prepare(settings)
    train(settings)
    return root / "params.toml"


def test_model_loads_once_and_requests_do_not_refit(config_path, monkeypatch):
    """Один запуск загружает один Pipeline, а HTTP-запросы не вызывают обучение."""
    real_load = api.load_model
    loads = []

    def count_load(path):
        loads.append(path)
        return real_load(path)

    def forbid_fit(*args, **kwargs):
        raise AssertionError("Веб-запрос не должен запускать обучение.")

    monkeypatch.setattr(api, "load_model", count_load)
    monkeypatch.setattr(Pipeline, "fit", forbid_fit)
    app = api.create_app(config_path)
    assert loads == []
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200 and health.json()["model_loaded"]
        info = client.get("/api/model-info")
        assert info.status_code == 200
        assert info.json()["categories"] == CATEGORIES
        assert info.json()["train_rows"] == 192
        for text in ("Курьер не привёз мой заказ", "Не могу войти в личный кабинет"):
            response = client.post("/api/predict", json={"text": text})
            assert response.status_code == 200
            data = response.json()
            assert data["model_version"] == info.json()["model_version"]
            assert data["data_version"] == info.json()["data_version"]
            assert set(data["probabilities"]) == set(CATEGORIES)
            assert sum(data["probabilities"].values()) == pytest.approx(1.0)
            assert data["probability"] == max(data["probabilities"].values())
            assert data["category_name"] == CATEGORIES[data["category"]]
        assert len(loads) == 1
    assert app.state.artifact is None


@pytest.mark.parametrize("body", [
    {}, {"text": ""}, {"text": "аа"}, {"text": "!!!"}, {"text": "1234"},
    {"text": 42}, {"text": None}, {"text": ["товар"]},
    {"text": "а" * 4001}, {"text": "Обращение", "extra": True},
])
def test_invalid_input_is_422_without_echoing_the_payload(config_path, body):
    with TestClient(api.create_app(config_path)) as client:
        response = client.post("/api/predict", json=body)
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)
        assert all("input" not in error for error in response.json()["detail"])
        assert response.headers["X-Request-ID"]


def test_malformed_json_is_422(config_path):
    with TestClient(api.create_app(config_path)) as client:
        response = client.post(
            "/api/predict", content='{"text":', headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422
        assert "JSON" in response.json()["detail"][0]["msg"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_unavailable_model_keeps_api_alive_but_blocks_predictions(tmp_path, corrupt):
    config = tmp_path / "params.toml"
    shutil.copyfile(PROJECT_ROOT / "params.toml", config)
    if corrupt:
        models = tmp_path / "models"
        models.mkdir()
        (models / "model.joblib").write_bytes(b"invalid-model")
    with TestClient(api.create_app(config)) as client:
        health = client.get("/api/health")
        assert health.status_code == 503
        assert health.json() == {
            "status": "unavailable", "model_loaded": False, "model_version": None,
        }
        assert client.get("/api/model-info").status_code == 503
        prediction = client.post("/api/predict", json={"text": "Не пришла посылка"})
        assert prediction.status_code == 503
        assert str(tmp_path) not in prediction.text
        assert client.get("/docs").status_code == 200
    logs = (tmp_path / "logs" / "support-router.log").read_text(encoding="utf-8")
    assert "API запущен без доступной модели" in logs
    assert not (tmp_path / "data" / "processed").exists()


def test_frontend_assets_do_not_replace_api_404_or_expose_project_files(config_path, tmp_path):
    frontend = tmp_path / "dist"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text("<html>Интерфейс</html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('Интерфейс')", encoding="utf-8")
    with TestClient(api.create_app(config_path, frontend_dir=frontend)) as client:
        assert "Интерфейс" in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
        missing = client.get("/api/unknown")
        assert missing.status_code == 404 and "Интерфейс" not in missing.text
        assert client.get("/assets/..%2Fparams.toml").status_code == 404
        assert client.get("/models/model.joblib").status_code == 404
        assert client.get("/params.toml").status_code == 404


def test_missing_frontend_has_an_actionable_error(config_path, tmp_path):
    with TestClient(api.create_app(config_path, frontend_dir=tmp_path / "not-built")) as client:
        assert client.get("/").status_code == 503
        assert client.get("/api/health").status_code == 200


def test_internal_failure_is_logged_and_hidden_from_the_client(config_path, monkeypatch):
    def fail_prediction(*args):
        raise RuntimeError("Диагностическая причина сбоя")

    monkeypatch.setattr(api, "predict", fail_prediction)
    with TestClient(api.create_app(config_path)) as client:
        response = client.post("/api/predict", json={"text": "Курьер задержался"})
        assert response.status_code == 500
        assert "Диагностическая причина" not in response.text
        request_id = response.headers["X-Request-ID"]
    logs = (config_path.parent / "logs" / "support-router.log").read_text(encoding="utf-8")
    assert request_id in logs and "Диагностическая причина сбоя" in logs


def test_logs_identify_requests_but_do_not_store_the_text(config_path):
    private_text = "СекретноеСодержимоеОбращения не пришла посылка"
    with TestClient(api.create_app(config_path)) as client:
        response = client.post("/api/predict", json={"text": private_text})
        assert response.status_code == 200
        request_id = response.headers["X-Request-ID"]
    logs = (config_path.parent / "logs" / "support-router.log").read_text(encoding="utf-8")
    assert request_id in logs and "route=/api/predict status=200" in logs
    assert private_text not in logs and "СекретноеСодержимоеОбращения" not in logs


def test_openapi_describes_the_operations(config_path):
    with TestClient(api.create_app(config_path)) as client:
        schema = client.get("/openapi.json").json()
        assert set(schema["paths"]) == {
            "/api/health", "/api/model-info", "/api/predict", "/api/monitoring", "/api/feedback",
        }
        assert schema["paths"]["/api/predict"]["post"]["requestBody"]["required"]
        assert "PredictionResponse" in schema["components"]["schemas"]
