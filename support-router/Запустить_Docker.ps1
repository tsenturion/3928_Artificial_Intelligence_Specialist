# Первая сборка скачает крупные базовые образы и зависимости; запустите этот скрипт самостоятельно.
# Docker Desktop должен быть запущен в режиме Linux-контейнеров.
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
