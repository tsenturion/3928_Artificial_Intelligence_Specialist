# Первая сборка скачает крупные базовые образы и зависимости; запустите этот скрипт самостоятельно.
# Docker Desktop должен быть запущен в режиме Linux-контейнеров.

<#
.SYNOPSIS
    Готовит модель через DVC, собирает Docker-образ и ждёт готовности API.
.DESCRIPTION
    Последовательность выпуска: DVC repro → verify → сборка образа → healthcheck.
    Сбой любого обязательного шага останавливает следующие. Готовый контейнер
    содержит модель; при запуске он её не переобучает.
.PARAMETER Port
    Порт компьютера для контейнерного API, по умолчанию 8080.
.EXAMPLE
    .\Запустить_Docker.ps1 -Port 8080
    Локальная сборка и запуск в работающем Docker Desktop с Linux Engine.
.NOTES
    Требуется PowerShell 7. Первая сборка загружает базовые образы и зависимости.
    Журналы и SQLite хранятся в именованном volume. Имя Compose-проекта влияет
    на имя volume: сохраняйте его при обновлении, если нужны прежние данные.
#>
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
$previousPort = $env:SUPPORT_ROUTER_PORT
try {
    $docker = (Get-Command docker -ErrorAction Stop).Source
    try {
        $engineType = & $docker info --format '{{.OSType}}' 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Docker info завершился с ошибкой."
        }
    } catch {
        throw "Docker Engine недоступен. Запустите Docker Desktop и повторите команду."
    }
    if ($engineType.Trim() -ne "linux") {
        throw "Нужен Docker Engine в режиме Linux-контейнеров."
    }

    $dvcScript = Join-Path $PSScriptRoot "Управление_DVC.ps1"
    if (-not (Test-Path -LiteralPath $dvcScript)) {
        throw "Не найден Управление_DVC.ps1 для подготовки модели."
    }
    & $dvcScript -Action repro
    if ($LASTEXITCODE -ne 0) { throw "DVC repro завершился с ошибкой." }
    # repro уже проверил соответствие отчёта и модели; внутри образа проверка
    # повторится в Linux-окружении с зависимостями рабочего контейнера.

    $env:SUPPORT_ROUTER_PORT = [string]$Port
    Push-Location $PSScriptRoot
    try {
        try {
            # --build собирает образ, -d оставляет сервис работать в фоне,
            # --wait возвращает успех только после готовности healthcheck.
            & $docker compose up --build -d --wait
            if ($LASTEXITCODE -ne 0) { throw "Docker Compose не смог запустить приложение." }
        } catch {
            try { & $docker compose logs --no-color --tail 100 app } catch { }
            throw
        }
    } finally {
        Pop-Location
    }
    Write-Host "Приложение: http://127.0.0.1:$Port"
} finally {
    if ($null -eq $previousPort) {
        Remove-Item Env:SUPPORT_ROUTER_PORT -ErrorAction SilentlyContinue
    } else {
        $env:SUPPORT_ROUTER_PORT = $previousPort
    }
}
