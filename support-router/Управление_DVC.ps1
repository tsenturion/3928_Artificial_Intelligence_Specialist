# Первый запуск: .\Управление_DVC.ps1 -Action init
# Пересчёт после изменений: .\Управление_DVC.ps1 -Action repro
# Сохранить артефакты в хранилище: .\Управление_DVC.ps1 -Action push
# Восстановить выбранную Git-версию: .\Управление_DVC.ps1 -Action pull
# Проверка комплекта без переобучения: .\Управление_DVC.ps1 -Action verify
#
# DVC устанавливается вместе с инструментами проекта:
# python -m pip install -e ".[dev,mlops]"
# Git хранит код, params.toml, .dvc/config, data/raw.dvc, dvc.yaml и dvc.lock.
# Содержимое данных и модели хранится отдельно. Сам DVC не делает git commit/push.
# Хранилище этого компьютера задаётся в .dvc/config.local (оно не попадёт в Git).
# На другой машине укажите существующее доступное хранилище через -StoragePath.

<#
.SYNOPSIS
    Управляет версиями данных, модели и результатами этапов DVC.
.DESCRIPTION
    Git хранит исходники и указатели, cache — локальные объекты DVC,
    remote — отдельную копию объектов для восстановления на другой машине.
    Локальный путь remote не является доступным облачным хранилищем для CI.
    GitHub Actions восстанавливает raw из опубликованного снимка в Release.
.PARAMETER Action
    init — настроить local, подготовить комплект и отправить объекты;
    repro — пересчитать изменившиеся этапы и проверить комплект;
    verify — проверить готовую модель с отчётом без пересчёта;
    push/pull — отправить/восстановить объекты, не отправляя коммит Git;
    status — показать изменения относительно текущих указателей.
.PARAMETER StoragePath
    Доступный путь DVC-хранилища вне всего Git-репозитория. При первом запуске
    без параметра создаётся локальное хранилище пользователя.
.EXAMPLE
    .\Управление_DVC.ps1 -Action init
    Первичная подготовка, когда data/raw уже присутствует на компьютере.
.EXAMPLE
    .\Управление_DVC.ps1 -Action pull -StoragePath "D:\DVC\support-router"
    Восстановление из существующего хранилища с нужными объектами.
.EXAMPLE
    gh release download support-router-data-v1.0.0-synthetic -R tsenturion/3928_Artificial_Intelligence_Specialist -p support-router-data.zip -D "$env:TEMP\support-router-bootstrap"
    Expand-Archive -LiteralPath "$env:TEMP\support-router-bootstrap\support-router-data.zip" -DestinationPath "$env:TEMP\support-router-bootstrap\storage"
    python -m dvc remote add --local bootstrap "$env:TEMP\support-router-bootstrap\storage"
    python -m dvc pull data/raw.dvc --remote bootstrap
    .\Управление_DVC.ps1 -Action init
    Первый запуск после git clone без прежнего локального remote. Команды
    выполняются из папки проекта в окружении с DVC; также нужен GitHub CLI.
    Версия Release должна соответствовать data.version в выбранном params.toml.
.NOTES
    Требуется PowerShell 7. init с пустым remote не восстановит отсутствующие
    объекты: сначала нужен исходный CSV или доступный снимок данных.
    Изменение файлов-источников, включая комментарии в deps, может вызвать
    пересчёт DVC; проверять текущую модель без него позволяет Action verify.
#>
[CmdletBinding()]
param(
    [ValidateSet("init", "repro", "verify", "push", "pull", "status")]
    [string]$Action = "repro",
    [string]$StoragePath
)

$ErrorActionPreference = "Stop"
$projectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $projectPython) {
    $pythonExecutable = $projectPython
} else {
    $pythonExecutable = (Get-Command python -ErrorAction Stop).Source
}

function Invoke-ProjectPython {
    # Ошибка внешней программы не всегда превращается в исключение PowerShell.
    # Явная проверка LASTEXITCODE делает остановку процесса подготовки обязательной.
    param([string[]]$PythonArguments)
    & $pythonExecutable @PythonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Команда Python завершилась с кодом $LASTEXITCODE. Подготовка остановлена."
    }
}

$previousPath = $env:PATH
$previousPythonPath = $env:PYTHONPATH
$previousUtf8 = $env:PYTHONUTF8
Push-Location $PSScriptRoot
try {
    # Важно, чтобы дочерние команды python внутри dvc.yaml использовали то же окружение.
    $env:PATH = (Split-Path -Parent $pythonExecutable) + [IO.Path]::PathSeparator + $previousPath
    $env:PYTHONPATH = Join-Path $PSScriptRoot "src"
    # Кодировка передаётся также дочерним процессам этапов dvc.yaml.
    $env:PYTHONUTF8 = "1"
    if ($previousPythonPath) {
        $env:PYTHONPATH += [IO.Path]::PathSeparator + $previousPythonPath
    }
    if ($Action -eq "verify") {
        Invoke-ProjectPython -PythonArguments @("-X", "utf8", "-m", "support_router", "verify")
        return
    }
    Invoke-ProjectPython -PythonArguments @("-c", "import dvc")
    if (-not (Test-Path -LiteralPath ".dvc\config")) {
        # --subdir позволяет держать DVC-проект в папке внутри Git-репозитория курса.
        Invoke-ProjectPython -PythonArguments @("-m", "dvc", "init", "--subdir")
    }
    Invoke-ProjectPython -PythonArguments @("-m", "dvc", "config", "core.analytics", "false")
    Invoke-ProjectPython -PythonArguments @("-m", "dvc", "config", "cache.type", "copy")
    # В глубокой папке курса временные файлы run-cache превышают лимит пути Windows.
    # Короткий локальный путь устраняет ошибку без изменения системных настроек.
    # Старый кеш не удаляем и не переносим. Абсолютный путь не попадёт в Git.
    $cachePath = Join-Path $env:USERPROFILE ".cache\support-router\dvc"
    New-Item -ItemType Directory -Path $cachePath -Force | Out-Null
    Invoke-ProjectPython -PythonArguments @("-m", "dvc", "config", "--local", "cache.dir", $cachePath)
    # config.local может содержать только cache.dir, поэтому проверяем именно remote,
    # а не существование файла локальной конфигурации.
    $needsLocalRemote = $true
    try {
        $configuredRemote = & $pythonExecutable -m dvc config --local remote.local.url 2>$null
        $needsLocalRemote = $LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($configuredRemote)
    } catch {
        $needsLocalRemote = $true
    }
    if ($StoragePath -or $needsLocalRemote) {
        if (-not $StoragePath) {
            $StoragePath = Join-Path $env:USERPROFILE ".local\share\support-router\dvc-store"
        }
        $StoragePath = [IO.Path]::GetFullPath($StoragePath)
        # Хранилище должно переживать замену рабочей копии и не попадать в её Git.
        $projectPrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
        if ($StoragePath.Equals($PSScriptRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $StoragePath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Выберите для DVC-хранилища папку вне support-router."
        }
        $gitRoot = & git rev-parse --show-toplevel
        if ($LASTEXITCODE -ne 0) { throw "Не найден Git-репозиторий проекта." }
        $gitRoot = [IO.Path]::GetFullPath($gitRoot.Trim())
        $gitPrefix = $gitRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
        if ($StoragePath.Equals($gitRoot, [StringComparison]::OrdinalIgnoreCase) -or
            $StoragePath.StartsWith($gitPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "DVC-хранилище должно находиться вне всего Git-репозитория."
        }
        New-Item -ItemType Directory -Path $StoragePath -Force | Out-Null
        Invoke-ProjectPython -PythonArguments @(
            "-m", "dvc", "remote", "add", "--local", "--force", "-d", "local", $StoragePath
        )
    }
    if ($Action -in @("init", "repro")) {
        # add обновляет указатель raw при наличии исходников, pull восстанавливает
        # выбранный снимок при их отсутствии. Саму цепочку вычислений запускает repro.
        if (Test-Path -LiteralPath "data\raw") {
            Invoke-ProjectPython -PythonArguments @("-m", "dvc", "add", "data/raw")
        } else {
            Invoke-ProjectPython -PythonArguments @("-m", "dvc", "pull")
        }
        Invoke-ProjectPython -PythonArguments @("-m", "dvc", "repro")
        Invoke-ProjectPython -PythonArguments @("-X", "utf8", "-m", "support_router", "verify")
        if ($Action -eq "init") {
            Invoke-ProjectPython -PythonArguments @("-m", "dvc", "push")
        }
    } elseif ($Action -eq "pull") {
        # Успешного копирования недостаточно: комплект должен пройти verify.
        Invoke-ProjectPython -PythonArguments @("-m", "dvc", "pull")
        Invoke-ProjectPython -PythonArguments @("-X", "utf8", "-m", "support_router", "verify")
    } elseif ($Action -eq "push") {
        Invoke-ProjectPython -PythonArguments @("-m", "dvc", "push")
    } else {
        Invoke-ProjectPython -PythonArguments @("-m", "dvc", "status")
    }
} finally {
    Pop-Location
    $env:PATH = $previousPath
    $env:PYTHONPATH = $previousPythonPath
    $env:PYTHONUTF8 = $previousUtf8
}
