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

<#
.SYNOPSIS
    Выполняет подготовку, обучение, оценку или одно предсказание.
.DESCRIPTION
    Обёртка над Python CLI. Выбирает окружение проекта, передаёт явный TOML
    и проверяет код завершения. run заново выполняет полный цикл; для
    пересчёта только изменившихся этапов используйте Управление_DVC.ps1.
.PARAMETER Command
    Операция run, prepare, train, evaluate или predict. По умолчанию run.
.PARAMETER Text
    Текст одного обращения для predict; для обучения не используется.
.EXAMPLE
    .\Запустить_модель.ps1 -Command predict -Text "Курьер не привёз заказ"
    Применяет уже сохранённый Pipeline, не обучая его повторно.
.NOTES
    Запуск в PowerShell 7 из папки проекта. Справка: Get-Help .\Запустить_модель.ps1 -Full.
#>
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
    # & вызывает найденный исполняемый файл, @arguments передаёт массив отдельных
    # аргументов. Текст обращения остаётся одним аргументом даже с пробелами.
    & $pythonExecutable @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Программа завершилась с кодом $LASTEXITCODE. Подробности находятся в logs."
    }
} finally {
    # finally сработает и при ошибке: окружение вызывающей сессии восстанавливается.
    $env:PYTHONPATH = $previousPythonPath
}
