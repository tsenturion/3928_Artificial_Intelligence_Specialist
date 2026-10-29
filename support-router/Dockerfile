# Node и инструменты Vite нужны только для сборки интерфейса.
FROM node:24-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/vite.config.js ./
COPY frontend/src ./src
RUN npm run build

# В рабочем образе остаются Python, готовый интерфейс и выбранная версия модели.
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUPPORT_ROUTER_CONFIG=/app/params.toml
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --no-cache-dir .
COPY params.toml ./
# Здесь нет обучения: копируется артефакт, который подготовлен и сохранён через DVC.
COPY models/model.joblib ./models/model.joblib
COPY reports/metrics.json ./reports/metrics.json
COPY --from=frontend /app/frontend/dist ./frontend/dist

# Проверка завершается ошибкой сборки, если отчёт не соответствует модели.
RUN python -m support_router --config /app/params.toml verify
RUN mkdir -p /app/logs && chown -R 10001:10001 /app/logs
# Запросы обслуживает непривилегированный пользователь; запись нужна только в logs.
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1
CMD ["python", "-m", "uvicorn", "support_router.api:app", "--host", "0.0.0.0", "--port", "8000"]
