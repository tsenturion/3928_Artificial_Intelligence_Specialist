# Готовит небольшой архив синтетических данных для GitHub Actions.
# Пример: .\Подготовить_данные_CI.ps1 -OutputPath "$env:TEMP\support-router-data.zip"
# DVC сам экспортирует только выбранный набор, локальный remote остаётся прежним.
#
# При новой версии данных сначала выполните dvc add data/raw, repro и commit.
# Создайте тег support-router-data-v<версия из params.toml> на этом коммите,
# отправьте тег и опубликуйте архив support-router-data.zip как asset его Release.
# Затем отправьте main: CI сможет восстановить данные до начала обучения.
# В архив нельзя включать реальные персональные обращения: репозиторий публичный.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$archivePath = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $archivePath) {
    throw "Файл уже существует: $archivePath. Укажите новое имя."
}
$projectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$pythonExecutable = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { (Get-Command python).Source }
$exportRoot = Join-Path ([IO.Path]::GetTempPath()) ("sr-ci-" + [guid]::NewGuid().ToString("N"))
$exportStorage = Join-Path $exportRoot "storage"
$remoteName = "export-ci-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$remoteAdded = $false
$previousUtf8 = $env:PYTHONUTF8
New-Item -ItemType Directory -Path $exportStorage -Force | Out-Null
Push-Location $PSScriptRoot
try {
    $env:PYTHONUTF8 = "1"
    $dataVersion = & $pythonExecutable -c "import tomllib; print(tomllib.load(open('params.toml', 'rb'))['data']['version'])"
    if ($LASTEXITCODE -ne 0) { throw "Не удалось прочитать версию данных." }
    & $pythonExecutable -m dvc remote add --local $remoteName $exportStorage
    if ($LASTEXITCODE -ne 0) { throw "Не удалось настроить временный экспорт DVC." }
    $remoteAdded = $true
    & $pythonExecutable -m dvc push data/raw.dvc --remote $remoteName
    if ($LASTEXITCODE -ne 0) { throw "Экспорт данных DVC завершился с ошибкой." }
    # Здесь нет чтения или удаления существующего кеша: архивируется новый экспорт.
    Compress-Archive -Path (Join-Path $exportStorage "*") -DestinationPath $archivePath
    Write-Host "Архив: $archivePath"
    Write-Host "Тег данных: support-router-data-v$dataVersion"
    Write-Host "Имя asset в GitHub Release: support-router-data.zip"
} finally {
    if ($remoteAdded) {
        & $pythonExecutable -m dvc remote remove --local $remoteName
        if ($LASTEXITCODE -ne 0) { Write-Warning "Удалите временный remote вручную: $remoteName" }
    }
    # Удаляется только созданный этим запуском экспорт, не существующие кеши DVC.
    # Проверяем абсолютный путь до рекурсивного удаления.
    $resolvedExport = (Resolve-Path -LiteralPath $exportRoot).ProviderPath
    $tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
    if (
        [IO.Path]::GetDirectoryName($resolvedExport).TrimEnd('\', '/') -eq $tempParent -and
        [IO.Path]::GetFileName($resolvedExport) -match '^sr-ci-[0-9a-f]{32}$'
    ) {
        Remove-Item -LiteralPath $resolvedExport -Recurse -Force
    } else {
        Write-Warning "Временный экспорт оставлен: путь не прошёл проверку."
    }
    $env:PYTHONUTF8 = $previousUtf8
    Pop-Location
}
