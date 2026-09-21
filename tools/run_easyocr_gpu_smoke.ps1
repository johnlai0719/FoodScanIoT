param([int]$Limit = 5)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& ./.venv-easyocr-cuda-clean/Scripts/python.exe tools/easyocr_detect_all.py --dataset 'D:/FoodScanIot/APP-sync-server/測試/量化測試' --output .artifacts/easyocr-det-gpu-smoke-v1 --model-dir .artifacts/easyocr-det-models --device cuda --limit $Limit
if ($LASTEXITCODE -ne 0) { throw 'GPU detection failed; no Gemini inference was requested.' }
