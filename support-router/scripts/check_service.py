"""Проверка собранного приложения по HTTP для CI и локального Docker Desktop.

Сначала --state сохраняет ID размеченного обращения. После пересоздания
контейнера --check-persistence проверяет сохранение этой записи в volume.
Это небольшие синтетические запросы, которые попадут в настоящую статистику.
"""

import argparse
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


HTTP = build_opener(ProxyHandler({}))


def request(base_url, path, payload=None, expected_status=200):
    """Проверяет HTTP-статус, не печатая тексты обращений или секреты."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    query = Request(base_url + path, data=body, headers={"Content-Type": "application/json"})
    try:
        response = HTTP.open(query, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        if response.status != expected_status:
            raise RuntimeError(f"{path}: ожидался HTTP {expected_status}, получен {response.status}.")
        content = response.read()
        if "application/json" in response.headers.get("Content-Type", ""):
            return json.loads(content)
        return content


def check(url, state_path, persistence=False):
    health = request(url, "/api/health")
    assert health["status"] == "ready" and health["model_loaded"]
    before = request(url, "/api/monitoring")
    if persistence:
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert before["model"] == saved["model"], "После запуска изменилась модель."
        assert before["traffic"]["requests"] == saved["requests"], "Потеряны запросы мониторинга."
        assert before["quality"]["labeled_count"] == saved["labeled"], "Потеряна разметка."
        # Успешная повторная запись той же метки подтверждает сохранение именно ID.
        request(url, "/api/feedback", {
            "request_id": saved["request_id"], "expected_category": "delivery",
        })
        assert request(url, "/api/monitoring")["quality"]["labeled_count"] == saved["labeled"]
        print("Проверка пройдена: модель, статистика и разметка сохранены после пересоздания.")
        return
    info = request(url, "/api/model-info")
    assert info["model_version"] == health["model_version"]
    html = request(url, "/").decode("utf-8")
    assert 'id="app"' in html
    assert 'id="app"' in request(url, "/monitoring").decode("utf-8")
    # Проверяем сами JS/CSS, чтобы пустая HTML-оболочка не считалась готовым фронтендом.
    assets = re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', html)
    assert assets, "В HTML нет ресурсов собранного интерфейса."
    for asset in assets:
        assert request(url, asset)
    prediction = request(url, "/api/predict", {"text": "Курьер не привёз заказ"})
    assert prediction["category"] == "delivery"
    assert set(prediction["probabilities"]) == {"delivery", "payment", "refund", "technical"}
    assert abs(sum(prediction["probabilities"].values()) - 1) < 1e-6
    request(url, "/api/feedback", {
        "request_id": prediction["request_id"], "expected_category": "delivery",
    })
    request(url, "/api/predict", {"text": "!!!"}, expected_status=422)
    after = request(url, "/api/monitoring")
    assert after["traffic"]["requests"] == before["traffic"]["requests"] + 2
    assert after["traffic"]["client_errors"] == before["traffic"]["client_errors"] + 1
    assert after["quality"]["labeled_count"] == before["quality"]["labeled_count"] + 1
    state_path.write_text(json.dumps({
        "request_id": prediction["request_id"], "model": after["model"],
        "requests": after["traffic"]["requests"], "labeled": after["quality"]["labeled_count"],
    }, ensure_ascii=False), encoding="utf-8")
    print("Проверка пройдена: интерфейс, API, разметка и учёт ошибок работают.")


def main():
    parser = argparse.ArgumentParser(description="Проверка работающего контейнера")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--check-persistence", action="store_true")
    args = parser.parse_args()
    parsed = urlparse(args.url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("Проверка рассчитана на локальный HTTP-адрес.")
    check(args.url.rstrip("/"), args.state, args.check_persistence)


if __name__ == "__main__":
    main()
