$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& ./.venv-easyocr-det/Scripts/python.exe tools/easyocr_detect_all.py --dataset 'D:/FoodScanIot/APP-sync-server/測試/量化測試' --output .artifacts/easyocr-det-177-v1 --model-dir .artifacts/easyocr-det-models --download
if ($LASTEXITCODE -ne 0) { throw 'Detection did not complete all cases; inference not started.' }
& python tools/easyocr_union_gemini.py --boxes .artifacts/easyocr-det-177-v1 --output .artifacts/easyocr-union-gemini-177-v1 --limit 0 --infer
if ($LASTEXITCODE -ne 0) { throw 'Inference stopped; inspect saved case records.' }
