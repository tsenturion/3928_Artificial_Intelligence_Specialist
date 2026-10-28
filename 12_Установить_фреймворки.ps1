# Подготовка полной среды для учебного ноутбука о фреймворках.
# Скрипт скачивает TensorFlow и, при необходимости, PyTorch.
# Запускается пользователем; права администратора обычно не требуются.
[CmdletBinding()]
param(
    [string]$PythonPath = 'python'
)

$ErrorActionPreference = 'Stop'
$environmentPath = Join-Path $PSScriptRoot '.venv_frameworks'
$environmentPython = Join-Path $environmentPath 'Scripts\python.exe'

function Invoke-CheckedPython {
    param(
        [string]$Executable,
        [string[]]$CommandArguments
    )
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python завершился с кодом $LASTEXITCODE. Подготовка остановлена."
    }
}

Write-Host 'Будет подготовлена отдельная среда .venv_frameworks рядом со скриптом.'
Write-Host 'TensorFlow для Windows Python 3.13 имеет размер около 335 МиБ без зависимостей.'
Write-Host 'При отсутствии PyTorch потребуется также его загрузка. Базовые пакеты Python не заменяются.'

if (-not (Test-Path -LiteralPath $environmentPython -PathType Leaf)) {
    Invoke-CheckedPython -Executable $PythonPath -CommandArguments @(
        '-m', 'venv', '--system-site-packages', $environmentPath
    )
}

Invoke-CheckedPython -Executable $environmentPython -CommandArguments @(
    '-X', 'utf8', '-c',
    "import sys; assert (3, 11) <= sys.version_info[:2] <= (3, 13), 'Для этой среды выберите Python 3.11, 3.12 или 3.13'; print(sys.executable, sys.version)"
)

Invoke-CheckedPython -Executable $environmentPython -CommandArguments @(
    '-X', 'utf8', '-m', 'pip', 'install', '--no-cache-dir', '--only-binary=:all:',
    'tensorflow==2.21.0', 'keras==3.15.1', 'h5py==3.14.0',
    'numpy==2.4.6', 'pandas==2.3.3', 'matplotlib==3.10.9',
    'scikit-learn==1.8.0', 'ipykernel==7.1.0'
)

$torchCheck = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('torch') else 1)"
& $environmentPython -c $torchCheck
if ($LASTEXITCODE -ne 0) {
    Invoke-CheckedPython -Executable $environmentPython -CommandArguments @(
        '-X', 'utf8', '-m', 'pip', 'install', '--no-cache-dir', '--only-binary=:all:', 'torch==2.11.0'
    )
}

$verificationCode = @'
import os
os.environ['KERAS_BACKEND'] = 'tensorflow'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '1'
import tensorflow as tf
import keras
import torch
print('TensorFlow:', tf.__version__)
print('Keras:', keras.__version__, 'backend:', keras.backend.backend())
print('PyTorch:', torch.__version__)
'@
Invoke-CheckedPython -Executable $environmentPython -CommandArguments @('-X', 'utf8', '-c', $verificationCode)

Invoke-CheckedPython -Executable $environmentPython -CommandArguments @(
    '-X', 'utf8', '-m', 'ipykernel', 'install', '--user',
    '--name', 'ai-course-frameworks', '--display-name', 'Python — основы фреймворков ИИ'
)

Write-Host 'Среда готова. В Jupyter выберите ядро «Python — основы фреймворков ИИ».'
Write-Host 'Перезапустите ядро и выполните весь ноутбук сверху вниз.'
Write-Host 'Смена backend Keras выполняется при новом запуске процесса, а не посреди работы ядра.'
