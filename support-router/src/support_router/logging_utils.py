"""Диагностика запусков с сохранением журналов не более 30 дней."""

from datetime import datetime, timedelta, timezone
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import re
import sys
import time


def configure_logging(log_dir: Path) -> logging.Logger:
    """Подключает консоль и ежедневный файл, удаляет только устаревшие журналы приложения."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("support_router")
    # Закрываем старые обработчики до очистки: Windows не удаляет открытые журналы.
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    # backupCount ограничивает количество архивов. Проверка возраста нужна ещё и
    # при редких запусках, когда между двумя журналами может пройти несколько месяцев.
    for path in log_dir.glob("support-router.log*"):
        if re.fullmatch(r"support-router\.log(?:\.\d{4}-\d{2}-\d{2})?", path.name):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    file_handler = TimedRotatingFileHandler(
        log_dir / "support-router.log", when="midnight", backupCount=30,
        encoding="utf-8", utc=True,
    )
    console_handler = logging.StreamHandler(sys.stderr)
    # Единый UTC-формат делает время событий однозначным между разными компьютерами.
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
    )
    formatter.converter = time.gmtime
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
