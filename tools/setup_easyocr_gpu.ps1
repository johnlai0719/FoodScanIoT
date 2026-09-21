$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath './.venv-easyocr-cuda-clean/Scripts/python.exe')) {
    & ./.venv-easyocr-det/Scripts/python.exe -m venv .venv-easyocr-cuda-clean
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create GPU environment.' }
}
& ./.venv-easyocr-cuda-clean/Scripts/python.exe -m pip install 'torch==2.11.0+cu128' 'torchvision==0.26.0+cu128' 'easyocr==1.7.2' --extra-index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'GPU package installation failed.' }
& ./.venv-easyocr-cuda-clean/Scripts/python.exe -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.__version__, torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) { throw 'GPU runtime verification failed.' }
