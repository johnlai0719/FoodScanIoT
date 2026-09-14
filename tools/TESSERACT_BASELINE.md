# Tesseract baseline

This experiment measures unmodified Tesseract OCR before connecting it to the
FoodScanIoT parsing pipeline. It does not use PaddleOCR, HunyuanOCR, Qwen, Gemini,
or any remote inference API.

## Build

```powershell
docker build -f tools/tesseract.Dockerfile -t foodscan-tesseract-baseline .
```

## Smoke test

```powershell
docker run --rm `
  -v "D:\FoodScanIot\_worktrees\adi-multimodal-compliance:/workspace" `
  -v "D:\FoodScanIot\APP-sync-server\測試\量化測試\images:/dataset-images:ro" `
  foodscan-tesseract-baseline `
  --cases "/workspace/測試/量化測試/cases.json" `
  --image-root /dataset-images `
  --output /workspace/.artifacts/tesseract-basic `
  --limit 5
```

The output keeps line text, average confidence, and four-corner bounding boxes
in the same basic shape as `PPOCR_TEST/run_baseline.py`. Treat it only as a reader
baseline until the existing field-level and additive-level evaluators have been
run against the same frozen case set.
