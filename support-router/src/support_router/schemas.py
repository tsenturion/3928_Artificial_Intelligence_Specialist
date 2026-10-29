"""Контракт HTTP API: какие данные принимает и возвращает сервис.

Pydantic проверяет JSON до вызова обработчика, а response_model проверяет ответ
перед отправкой. Эти же описания FastAPI включает в OpenAPI и страницу /docs.
Типы защищают границы API; они не заменяют проверку данных для обучения.
В обычной Python-функции аннотация сама по себе ввод не проверяет:
здесь проверку выполняет именно Pydantic при создании модели из запроса.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from support_router.data import MAX_TEXT_LENGTH, normalize_text


# Literal ограничивает допустимые идентификаторы; Field задаёт числовые границы.
Category = Literal["delivery", "payment", "refund", "technical"]
Probability = Annotated[float, Field(ge=0, le=1)]


class PredictionRequest(BaseModel):
    """JSON с одним текстом; числа, пустые строки и лишние поля отклоняются."""

    # Запрещаем незаявленные поля: опечатка в запросе не должна молча игнорироваться.
    model_config = ConfigDict(extra="forbid")
    text: str = Field(
        strict=True, min_length=3, max_length=MAX_TEXT_LENGTH,
        description="Текст обращения, содержащий буквы.",
        examples=["Курьер не привёз мой заказ"],
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """Дополнительно проверяет смысл строки и возвращает нормализованный текст."""
        # Field проверяет тип и длину, а валидатор отвергает, например, "!!!".
        # Та же обработка используется при обучении и в сохранённом Pipeline.
        return normalize_text(value)


class PredictionResponse(BaseModel):
    """Оценки классификатора не являются гарантией правильности ответа."""

    category: Category
    category_name: str
    probability: Probability
    # Ключи — идентификаторы из classes_ модели, значения — числовые оценки.
    probabilities: dict[Category, Probability]
    model_version: str
    data_version: str
    request_id: str


class FeedbackRequest(BaseModel):
    """Разметка приходит отдельно: предсказание нельзя выдавать за правильный ответ."""

    model_config = ConfigDict(extra="forbid")
    # Формат соответствует uuid4().hex, который сервер вернул в ответе.
    # Правильный формат ещё не означает существование записи: это проверяет SQLite.
    request_id: str = Field(strict=True, pattern=r"^[0-9a-f]{32}$")
    expected_category: Category


class FeedbackResponse(BaseModel):
    """Подтверждает принятую метку; не содержит оценки качества всей модели."""

    request_id: str
    expected_category: Category


class HealthResponse(BaseModel):
    """Готовность к предсказанию: HTTP 200 при загруженной модели, иначе 503."""

    status: Literal["ready", "unavailable"]
    model_loaded: bool
    model_version: str | None


class ModelInfoResponse(BaseModel):
    """Сведения именно о загруженной модели, а не о будущих настройках обучения."""

    model_version: str
    data_version: str
    created_at: str
    categories: dict[Category, str]
    train_rows: int
    features: int
    sklearn_version: str
    data_origin: str
