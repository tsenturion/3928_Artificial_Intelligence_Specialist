# Запуск из папки проекта: .\Запустить_приложение.ps1
# Из папки курса: .\support-router\Запустить_приложение.ps1
#
# Перед первым запуском подготовьте модель: .\Запустить_модель.ps1
# При необходимости установите зависимости: python -m pip install -e ".[dev]"
# Требуется Node.js 22.12+ или 24+. npm ci устанавливает версии из package-lock.json.
#
# По умолчанию интерфейс собирается Vite, затем FastAPI отдаёт его по одному адресу
# с API: http://127.0.0.1:8000. Ctrl+C останавливает приложение.
# -SkipFrontendBuild позволяет повторно запустить сервер с уже готовой сборкой.
#
# Для правки фронтенда можно отдельно выполнить в frontend команду npm run dev.
# Vite будет пересылать /api на работающий FastAPI (по умолчанию порт 8000).

<#
.SYNOPSIS
    Запускает FastAPI с готовой моделью и интерфейсом по одному адресу.
.DESCRIPTION
    Модель должна быть подготовлена заранее. Скрипт проверяет сборку интерфейса,
    задаёт путь конфигурации и запускает один процесс Uvicorn. Модель и SQLite
    открываются в lifespan приложения, HTTP-документация доступна по /docs.
.PARAMETER Port
    Локальный порт сервиса, по умолчанию 8000.
.PARAMETER SkipFrontendBuild
    Использует уже существующую сборку интерфейса.
.EXAMPLE
    .\Запустить_приложение.ps1 -SkipFrontendBuild -Port 8000
    Повторный запуск с готовой сборкой; Ctrl+C останавливает процесс.
.NOTES
    Команда не обучает модель. Пояснения к ML и HTTP-контракту находятся в
    src/support_router/model.py, schemas.py и api.py. Требуется PowerShell 7.
#>
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "models\model.joblib"))) {
    throw "Модель не найдена. Сначала выполните Запустить_модель.ps1."
}

$projectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $projectPython) {
    $pythonExecutable = $projectPython
} else {
    $pythonExecutable = (Get-Command python -ErrorAction Stop).Source
}

if (-not $SkipFrontendBuild) {
    # npm.cmd не зависит от политики выполнения npm.ps1 в Windows PowerShell.
    $npmExecutable = (Get-Command npm.cmd -ErrorAction Stop).Source
    Push-Location (Join-Path $PSScriptRoot "frontend")
    try {
        & $npmExecutable ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости фронтенда." }
        & $npmExecutable run build
        if ($LASTEXITCODE -ne 0) { throw "Не удалось собрать интерфейс." }
    } finally {
        Pop-Location
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "frontend\dist\index.html"))) {
    throw "Сборка интерфейса не найдена. Запустите скрипт без -SkipFrontendBuild."
}

$previousPythonPath = $env:PYTHONPATH
# Настройки передаются только запускаемому процессу и затем возвращаются.
# Это позволяет иметь несколько учебных проектов с разными конфигурациями.
$previousConfig = $env:SUPPORT_ROUTER_CONFIG
try {
    $env:PYTHONPATH = Join-Path $PSScriptRoot "src"
    if ($previousPythonPath) {
        $env:PYTHONPATH += [IO.Path]::PathSeparator + $previousPythonPath
    }
    $env:SUPPORT_ROUTER_CONFIG = Join-Path $PSScriptRoot "params.toml"
    Write-Host "Приложение: http://127.0.0.1:$Port"
    Write-Host "Документация API: http://127.0.0.1:$Port/docs"
    # Один процесс — один загруженный Pipeline и один файл диагностического журнала.
    & $pythonExecutable -X utf8 -m uvicorn support_router.api:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "Сервер завершился с кодом $LASTEXITCODE. Подробности находятся в logs."
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:SUPPORT_ROUTER_CONFIG = $previousConfig
}
