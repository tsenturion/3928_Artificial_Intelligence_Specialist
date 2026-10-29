"""FastAPI-приложение для предсказаний и выдачи собранного интерфейса.

Запуск из папки проекта после установки зависимостей:
    python -m uvicorn support_router.api:app --host 127.0.0.1 --port 8000

Документация HTTP API доступна по /docs. Обучение выполняется отдельной командой
python -m support_router run: веб-сервис никогда не запускает fit самостоятельно.

Жизненный цикл запроса: проверка JSON по Pydantic → получение готового артефакта
через Depends → предсказание → проверка ответа → запись служебных показателей.
Загрузка модели относится к запуску процесса, а не к каждому HTTP-запросу.
SQLite и файловый журнал решают разные задачи: агрегаты работы и разбор сбоев.
"""

from contextlib import asynccontextmanager
from asyncio import to_thread
import logging
import os
from pathlib import Path
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from support_router import __version__
from support_router.config import DEFAULT_CONFIG, load_settings
from support_router.logging_utils import configure_logging
from support_router.model import load_model, predict
from support_router.monitoring import MonitoringStore
from support_router.text_profile import text_features
from support_router.schemas import (
    HealthResponse, ModelInfoResponse, PredictionRequest, PredictionResponse,
    FeedbackRequest, FeedbackResponse,
)


LOGGER = logging.getLogger("support_router")
UNAVAILABLE_MESSAGE = "Модель недоступна. Подготовьте модель и перезапустите сервис."


def get_artifact(request: Request) -> dict:
    """Возвращает загруженный комплект или прекращает обработку с HTTP 503.

    Request даёт доступ к общему состоянию приложения. Проверка оформлена
    зависимостью, чтобы model-info и predict одинаково обрабатывали отсутствие
    модели; создавать и обучать новую модель в ответ на запрос здесь нельзя.
    """
    artifact = request.app.state.artifact
    if artifact is None:
        raise HTTPException(status_code=503, detail=UNAVAILABLE_MESSAGE)
    return artifact


# Annotated сохраняет тип dict и добавляет инструкцию FastAPI: значение аргумента
# нужно получить вызовом get_artifact, а не прочитать из присланного JSON.
Artifact = Annotated[dict, Depends(get_artifact)]


def create_app(
    config_path: Path | str | None = None,
    frontend_dir: Path | None = None,
) -> FastAPI:
    """Собирает маршруты и жизненный цикл, пока не открывая файлы модели.

    Явный config_path удобен для проверок в изолированной временной папке.
    В рабочем контейнере SUPPORT_ROUTER_CONFIG указывает на /app/params.toml.
    frontend_dir задаёт каталог готовой сборки, которую отдаёт тот же сервер.
    Такой фабричный подход позволяет создавать отдельные экземпляры приложения.
    """
    config_path = Path(
        config_path or os.environ.get("SUPPORT_ROUTER_CONFIG", DEFAULT_CONFIG)
    ).resolve()
    frontend_dir = (
        Path(frontend_dir).resolve()
        if frontend_dir is not None else config_path.parent / "frontend" / "dist"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Открывает ресурсы один раз и освобождает их при завершении сервера."""
        # До yield выполняется запуск, после yield — завершение процесса.
        # В каждом процессе Uvicorn хранится один экземпляр готового Pipeline.
        logger = configure_logging(config_path.parent / "logs")
        # app.state живёт весь срок работы данного экземпляра приложения.
        # Это не хранилище, общее для нескольких процессов или компьютеров.
        app.state.artifact = None
        app.state.monitoring = None
        # Мониторинг и загрузка модели независимы: сбой SQLite не запрещает
        # классификацию, а отсутствие модели не мешает прочитать ошибки сервиса.
        try:
            # Папка logs уже сохраняется Docker volume; SQLite переживает перезапуск.
            app.state.monitoring = MonitoringStore(config_path.parent / "logs" / "monitoring.sqlite3")
        except Exception:
            logger.exception("Не удалось открыть хранилище мониторинга.")
        try:
            settings = load_settings(config_path)
            app.state.artifact = load_model(settings.model_path)
            logger.info(
                "API готов: версия модели=%s, версия данных=%s.",
                app.state.artifact["metadata"]["model_version"],
                app.state.artifact["metadata"]["data_version"],
            )
        except Exception:
            # Интерфейс и /docs остаются доступны, а readiness и predict вернут 503.
            # Причина и traceback остаются в журнале, локальные пути клиенту не выдаются.
            logger.exception("API запущен без доступной модели.")
        try:
            # yield передаёт управление FastAPI для обслуживания запросов.
            # При остановке выполнение продолжится в finally для закрытия ресурсов.
            yield
        finally:
            app.state.artifact = None
            if app.state.monitoring is not None:
                app.state.monitoring.close()
                app.state.monitoring = None
            logger.info("API остановлен.")
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    app = FastAPI(
        title="Распределение обращений",
        description="Предсказание категории обращения с помощью сохранённого Pipeline.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.artifact = None
    app.state.monitoring = None

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        """Оборачивает обработку любого HTTP-запроса диагностикой.

        call_next передаёт запрос следующему обработчику. request.state хранит
        сведения только текущего запроса; UUID связывает ответ, журнал и разметку.
        В агрегаты мониторинга попадают исключительно POST /api/predict.
        """
        request_id = uuid4().hex
        request.state.request_id = request_id
        # Монотонный таймер подходит для длительности: перевод системных часов
        # не создаст отрицательное время обработки.
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            LOGGER.exception("Ошибка обработки HTTP-запроса; request_id=%s.", request_id)
            response = JSONResponse(
                status_code=500,
                content={"detail": "Не удалось обработать запрос. Попробуйте позже."},
            )
        # Используем шаблон маршрута, а не произвольный URL или параметры пользователя.
        route = request.scope.get("route")
        route_name = getattr(route, "path", "неизвестный маршрут")
        # Измеряется обработка до сохранения метрики, без сети клиента и самой
        # записи в SQLite. Это не полное время ожидания, видимое в браузере.
        elapsed_ms = (perf_counter() - started) * 1000
        if request.method == "POST" and request.url.path == "/api/predict":
            store = request.app.state.monitoring
            artifact = request.app.state.artifact
            if store is not None:
                try:
                    # Дисковая запись не блокирует цикл HTTP-сервера.
                    await to_thread(
                        store.record,
                        request_id, response.status_code, elapsed_ms,
                        artifact["metadata"] if artifact else None,
                        getattr(request.state, "prediction_details", None),
                    )
                except Exception:
                    # Сбой наблюдения не должен превращать успешное предсказание в ошибку.
                    LOGGER.exception("Не удалось сохранить метрику запроса; request_id=%s.", request_id)
        LOGGER.info(
            "HTTP method=%s route=%s status=%s duration_ms=%.1f request_id=%s.",
            request.method, route_name, response.status_code,
            elapsed_ms, request_id,
        )
        # Заголовок возвращается и при 422/500; успешный JSON содержит тот же ID.
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exception: RequestValidationError):
        """Возвращает понятные ошибки 422 без эха исходного текста и тела запроса."""
        messages = {
            "missing": "Передайте поле text с текстом обращения.",
            "string_type": "Текст обращения должен быть строкой.",
            "string_too_short": "Введите не менее трёх символов.",
            "string_too_long": "Текст не должен превышать 4000 символов.",
            "extra_forbidden": "Лишние поля в запросе запрещены.",
            "json_invalid": "Не удалось прочитать JSON запроса.",
        }
        details = []
        for error in exception.errors():
            message = messages.get(error["type"], "Некорректные данные запроса.")
            if error["type"] == "value_error":
                message = str(error.get("ctx", {}).get("error", message))
            details.append({"loc": list(error["loc"]), "msg": message, "type": error["type"]})
        return JSONResponse(status_code=422, content={"detail": details})

    # Декоратор связывает HTTP-метод и URL с функцией. response_model задаёт
    # проверку ответа и его схему, summary — короткую подпись на странице /docs.
    @app.get(
        "/api/health", response_model=HealthResponse,
        summary="Проверить готовность модели", responses={503: {"model": HealthResponse}},
    )
    def health(request: Request, response: Response):
        """Показывает готовность загруженной модели принимать обращения.

        HTTP 200 означает, что модель загружена; HTTP 503 — что она недоступна.
        Docker healthcheck использует этот маршрут. Он не запускает обучение,
        не оценивает качество на новых данных и не проверяет исправность SQLite.
        """
        artifact = request.app.state.artifact
        if artifact is None:
            response.status_code = 503
            return {"status": "unavailable", "model_loaded": False, "model_version": None}
        return {
            "status": "ready", "model_loaded": True,
            "model_version": artifact["metadata"]["model_version"],
        }

    @app.get(
        "/api/model-info", response_model=ModelInfoResponse,
        summary="Получить сведения о модели", responses={503: {"description": "Модель недоступна"}},
    )
    def model_info(artifact: Artifact):
        """Возвращает версии и сведения о модели, находящейся сейчас в памяти.

        Изменение params.toml или файла модели на диске само по себе не заменяет
        уже загруженный артефакт: для его смены процесс нужно перезапустить.
        """
        # Из полного артефакта выдаются только явно перечисленные поля контракта.
        return {name: artifact["metadata"][name] for name in ModelInfoResponse.model_fields}

    @app.post(
        "/api/predict", response_model=PredictionResponse,
        summary="Определить категорию обращения",
        responses={503: {"description": "Модель недоступна"}},
    )
    def predict_request(payload: PredictionRequest, request: Request, artifact: Artifact):
        """Классифицирует один текст и возвращает оценки четырёх категорий.

        request_id нужен для последующей передачи правильной категории через
        /api/feedback. Вероятность является оценкой модели; истинная категория
        не известна сервису, пока не получена независимая разметка.
        """
        # Обычный def FastAPI выполняет в пуле потоков, не блокируя цикл HTTP-сервера.
        # Здесь только predict_proba готовой модели: нет обучения или чтения модели с диска.
        result = predict(artifact, payload.text)
        request.state.prediction_details = {
            "category": result["category"], "probability": result["probability"],
            **text_features(artifact["pipeline"], payload.text),
        }
        # Распаковка **result дополняет ответ идентификатором без изменения
        # функции predict, которую также использует командная строка.
        return {**result, "request_id": request.state.request_id}

    def get_monitoring(request: Request) -> MonitoringStore:
        """Передаёт обработчику открытое хранилище либо возвращает HTTP 503."""
        if request.app.state.monitoring is None:
            raise HTTPException(status_code=503, detail="Хранилище мониторинга недоступно.")
        return request.app.state.monitoring

    @app.get("/api/monitoring", summary="Посмотреть метрики сервиса и текущей модели")
    def monitoring(
        request: Request, hours: Annotated[int, Query(ge=1, le=720)] = 24,
        store: MonitoringStore = Depends(get_monitoring),
    ) -> dict:
        """Собирает показатели за последние hours часов, от 1 до 720.

        Запросы и ошибки учитываются для всего сервиса. Категории, профиль
        входов и качество по разметке относятся только к текущему обучению.
        Без разметки значения accuracy и macro_f1 возвращаются как null.
        """
        artifact = request.app.state.artifact
        return store.snapshot(artifact["metadata"] if artifact else None, hours)

    @app.post("/api/feedback", response_model=FeedbackResponse, summary="Передать правильную категорию")
    def feedback(payload: FeedbackRequest, store: MonitoringStore = Depends(get_monitoring)):
        """Сохраняет правильную категорию для успешного запроса по request_id.

        Повторная отправка обновляет прежнюю метку и не увеличивает число
        размеченных обращений. Неизвестный, ошибочный или удалённый по сроку
        хранения запрос возвращает HTTP 404. Переобучение здесь не выполняется.
        """
        try:
            store.label(payload.request_id, payload.expected_category)
        except KeyError:
            raise HTTPException(status_code=404, detail="Успешный запрос не найден или срок хранения истёк.") from None
        return payload

    @app.get("/", include_in_schema=False)
    @app.get("/monitoring", include_in_schema=False)
    def frontend():
        index = frontend_dir / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail="Интерфейс не собран. В папке frontend выполните npm ci и npm run build.",
            )
        return FileResponse(index)

    # Отдаём только ресурсы сборки; неизвестные /api/* не подменяются страницей Vue.
    assets = frontend_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")
    return app


app = create_app()
