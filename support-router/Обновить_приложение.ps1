# Скачивание готового образа и запуск в Docker Desktop без Python, Node и DVC.
# Запустите самостоятельно: первая загрузка образа может быть объёмной.
# .\Обновить_приложение.ps1
# Для точной версии или отката укажите -Image "ghcr.io/.../support-router:sha-<коммит>".
#
# По умолчанию GitHub создаёт пакет GHCR закрытым. Для такого пакета предварительно
# выполните docker login ghcr.io со своим именем GitHub и токеном read:packages.
# Токен вводится в запросе Docker, его нельзя помещать в этот файл.
# Обновление сохраняет volume журналов; автоматическое обучение здесь не выполняется.

<#
.SYNOPSIS
    Развёртывает уже проверенный образ GHCR без локальной сборки.
.DESCRIPTION
    Сначала получает образ и выполняет verify в одноразовом контейнере.
    Только затем обновляет сервис Compose и ждёт его готовности.
    Ошибка скачивания или verify оставляет прежний рабочий контейнер.
    Если не прошёл healthcheck после замены, изучите журнал и укажите прежний тег:
    автоматического отката в этом учебном варианте нет.
.PARAMETER Image
    Полный тег GHCR. main указывает на последний успешно доставленный выпуск;
    тег sha-<коммит> выбирает выпуск по версии исходного кода.
.PARAMETER Port
    Локальный порт приложения, по умолчанию 8080.
.EXAMPLE
    .\Обновить_приложение.ps1 -Port 8080
    Загружает проверенный main и сохраняет прежний volume мониторинга.
.NOTES
    Требуется PowerShell 7 и Docker с Linux Engine. Доступ к закрытому пакету
    настраивается через docker login ghcr.io. Скрипт не хранит токен и не
    изменяет обучающие данные. Имя Compose-проекта должно сохраняться при обновлении.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^ghcr\.io/[a-z0-9._/-]+:[a-zA-Z0-9_.-]+$')]
    [string]$Image = "ghcr.io/tsenturion/3928_artificial_intelligence_specialist/support-router:main",
    [ValidateRange(1024, 65535)]
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
$previousImage = $env:SUPPORT_ROUTER_IMAGE
$previousPort = $env:SUPPORT_ROUTER_PORT
$docker = (Get-Command docker -ErrorAction Stop).Source
Push-Location $PSScriptRoot
try {
    $engineType = & $docker info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or $engineType.Trim() -ne "linux") {
        throw "Запустите Docker Desktop с Linux Engine."
    }
    $env:SUPPORT_ROUTER_IMAGE = $Image
    $env:SUPPORT_ROUTER_PORT = [string]$Port
    & $docker compose -f compose.release.yaml pull app
    if ($LASTEXITCODE -ne 0) {
        throw "Образ не скачан; прежний контейнер не заменён. Проверьте тег и docker login ghcr.io."
    }
    # Перед заменой работающего приложения проверяем комплект внутри нового образа.
    # Пакет установлен в site-packages, а конфигурация образа находится в /app.
    & $docker run --rm --entrypoint python $Image -m support_router --config /app/params.toml verify
    if ($LASTEXITCODE -ne 0) { throw "Комплект модели не прошёл проверку; обновление остановлено." }
    # Образ уже получен и проверен: --pull never исключает его смену между
    # проверкой комплекта и запуском. --wait проверяет healthcheck, не качество ML.
    & $docker compose -f compose.release.yaml up -d --wait --wait-timeout 90 --pull never
    if ($LASTEXITCODE -ne 0) {
        & $docker compose -f compose.release.yaml logs --no-color --tail 100 app
        throw "Приложение не прошло healthcheck. Проверьте журнал; для отката задайте прежний -Image."
    }
    Write-Host "Образ: $Image"
    Write-Host "Приложение: http://127.0.0.1:$Port"
    Write-Host "Мониторинг: http://127.0.0.1:$Port/monitoring"
} finally {
    $env:SUPPORT_ROUTER_IMAGE = $previousImage
    $env:SUPPORT_ROUTER_PORT = $previousPort
    Pop-Location
}
