"""Небольшой поток синтетических запросов для проверки живого сервиса.

Из папки проекта при запущенном API:
    python -m support_router.demo_monitoring
    python -m support_router.demo_monitoring --shift

Запросы намеренно попадают в настоящие метрики. Первая группа берётся из
синтетического контроля и получает известные метки; сдвинутые тексты не размечаем.
"""

import argparse
from collections import defaultdict
import json
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlparse

from support_router.config import load_settings
from support_router.data import read_records


# Для localhost не используем системный прокси Windows и переменные HTTP_PROXY.
# Это настройка только данного клиента, параметры операционной системы не меняются.
LOCAL_HTTP = build_opener(ProxyHandler({}))


def send(base_url: str, path: str, payload: dict) -> dict:
    request = Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with LOCAL_HTTP.open(request, timeout=15) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Синтетические запросы для мониторинга")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--shift", action="store_true", help="Дополнительно отправить 24 необычных текста")
    args = parser.parse_args()
    # Демонстрация рассчитана на локальное приложение, а не на сторонние сервисы.
    parsed = urlparse(args.url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("Укажите локальный HTTP-адрес приложения.")
    settings = load_settings()
    rows = read_records(settings.source, raw=True)
    by_category = defaultdict(list)
    for row in rows:
        if row["split"] == "test":
            by_category[row["category"]].append(row)
    count = 0
    for partition in by_category.values():
        for row in partition[:6]:
            prediction = send(args.url, "/api/predict", {"text": row["text"]})
            send(args.url, "/api/feedback", {
                "request_id": prediction["request_id"], "expected_category": row["category"],
            })
            count += 1
    print(f"Отправлено и размечено синтетических обращений: {count}.")
    if args.shift:
        for index in range(24):
            # Длинный посторонний текст даёт наблюдаемый сигнал по длине и словарю.
            # Правильной категории из четырёх направлений у него не задано.
            text = ("Квантовая спектроскопия нейтринных осцилляций щщщ " * 25) + str(index)
            send(args.url, "/api/predict", {"text": text})
        print("Дополнительно отправлено 24 неразмеченных текста с другими характеристиками.")


if __name__ == "__main__":
    main()
