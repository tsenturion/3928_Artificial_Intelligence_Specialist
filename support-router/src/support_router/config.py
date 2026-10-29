"""Чтение настроек и единые пути к результатам проекта.

params.toml хранится в Git: изменение параметров видно при сравнении коммитов,
а DVC использует выбранные поля как зависимости этапов. Модель и отчёт получают
версии из одного объекта Settings, чтобы не расходились названия выпусков.
"""

from dataclasses import dataclass
from pathlib import Path
import tomllib


# Это путь для исходников в структуре проекта, а не поиск в текущем каталоге.
# После установки wheel в контейнер CLI получает явный --config /app/params.toml,
# FastAPI — переменную окружения SUPPORT_ROUTER_CONFIG.
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "params.toml"


@dataclass(frozen=True)
class Settings:
    """Неизменяемый набор параметров, общий для подготовки, обучения и оценки.

    root — каталог выбранного params.toml. Все вычисляемые пути строятся от него,
    поэтому смена текущей рабочей папки не перенаправит результаты в другое место.
    """

    # dataclass создаёт конструктор по полям, frozen запрещает их случайную замену.
    root: Path
    source: Path
    data_version: str
    model_version: str
    random_state: int
    ngram_range: tuple[int, int]
    regularization_c: float
    max_iter: int
    min_macro_f1: float

    @property
    def processed_dir(self) -> Path:
        """Каталог подготовленных выборок; свойство вычисляет путь без создания."""
        return self.root / "data" / "processed"

    @property
    def model_path(self) -> Path:
        """Единое место записи и загрузки комплекта модели."""
        return self.root / "models" / "model.joblib"

    @property
    def reports_dir(self) -> Path:
        """Каталог подробного отчёта, числовых метрик и контрольных прогнозов."""
        return self.root / "reports"


def load_settings(path: Path = DEFAULT_CONFIG) -> Settings:
    """Читает TOML, проверяет основные границы и возвращает Settings.

    Это чтение настроек, а не проверка существования всех входных файлов:
    данные, подготовленный набор и модель проверяют соответствующие операции.
    """
    path = path.resolve()
    # with закроет файл даже при исключении; tomllib читает TOML в двоичном режиме.
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    data, model, evaluation = config["data"], config["model"], config["evaluation"]
    # Из двух удобных для TOML чисел собираем tuple, который ожидает sklearn.
    ngrams = (int(model["ngram_min"]), int(model["ngram_max"]))
    if not 1 <= ngrams[0] <= ngrams[1] <= 8:
        raise ValueError("Размеры символьных фрагментов должны удовлетворять 1 ≤ min ≤ max ≤ 8.")
    if model["C"] <= 0 or model["max_iter"] < 1:
        raise ValueError("C и max_iter должны быть положительными.")
    if not 0 <= evaluation["min_macro_f1"] <= 1:
        raise ValueError("Порог macro-F1 должен находиться между 0 и 1.")
    for value in (data["version"], model["version"]):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Версии данных и модели должны быть непустыми строками.")
    # Явные int/float приводят параметры к типам, ожидаемым библиотеками,
    # а source разрешается относительно каталога конфигурации.
    return Settings(
        root=path.parent,
        source=(path.parent / data["source"]).resolve(),
        data_version=data["version"],
        model_version=model["version"],
        random_state=int(model["random_state"]),
        ngram_range=ngrams,
        regularization_c=float(model["C"]),
        max_iter=int(model["max_iter"]),
        min_macro_f1=float(evaluation["min_macro_f1"]),
    )
