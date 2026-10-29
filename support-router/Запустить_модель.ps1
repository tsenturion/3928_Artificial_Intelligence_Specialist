# Откройте PowerShell в папке проекта:
# .\Запустить_модель.ps1
# .\Запустить_модель.ps1 -Command predict -Text "Курьер не привёз мой заказ"
#
# Для отдельного окружения на другом компьютере:
# python -m venv .venv
# .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# Затем этот скрипт автоматически выберет Python из .venv.
#
# Используется Python из .venv проекта, если она есть, иначе команда python из PATH.
# При отсутствии зависимостей установите проект: python -m pip install -e ".[dev]"
# Запуск по умолчанию выполняет подготовку данных, обучение и оценку.
[CmdletBinding()]
param(
    [ValidateSet("run", "prepare", "train", "evaluate", "predict")]
    [string]$Command = "run",
    [string]$Text
)

$ErrorActionPreference = "Stop"
$projectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $projectPython) {
    $pythonExecutable = $projectPython
} else {
    $pythonExecutable = (Get-Command python -ErrorAction Stop).Source
}
if ($Command -eq "predict" -and [string]::IsNullOrWhiteSpace($Text)) {
    throw "Для predict передайте текст через параметр -Text."
}

# PYTHONPATH даёт Python доступ к папке src без установки самого пакета.
# Сохраняем прежнее значение, чтобы запуск не менял настройки текущей сессии.
$previousPythonPath = $env:PYTHONPATH
try {
    $sourcePath = Join-Path $PSScriptRoot "src"
    $env:PYTHONPATH = $sourcePath
    if ($previousPythonPath) {
        $env:PYTHONPATH += [IO.Path]::PathSeparator + $previousPythonPath
    }
    $arguments = @(
        "-X", "utf8", "-m", "support_router",
        "--config", (Join-Path $PSScriptRoot "params.toml"), $Command
    )
    if ($Command -eq "predict") {
        $arguments += $Text
    }
    & $pythonExecutable @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Программа завершилась с кодом $LASTEXITCODE. Подробности находятся в logs."
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
