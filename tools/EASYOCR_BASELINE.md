# EasyOCR baseline

This exploratory reader uses EasyOCR 1.7.2 with Traditional Chinese and English
(`ch_tra`, `en`). It emits the same line text, confidence, and four-point boxes as
the Tesseract baseline so both readers can be compared on the same frozen cases.

EasyOCR is Apache-2.0 and its default detector is based on NAVER/Clova CRAFT.
However, the provenance of the bundled Traditional Chinese recognition training
data is not documented precisely enough for this repository to treat the model as
competition-approved without a separate compliance review.

## Build

```powershell
docker build -f tools/easyocr.Dockerfile -t foodscan-easyocr-baseline .
```

## GPU smoke test

```powershell
docker run --rm --gpus all `
  -v "D:\FoodScanIot\_worktrees\adi-multimodal-compliance:/workspace" `
  -v "D:\FoodScanIot\APP-sync-server\測試\量化測試\images:/dataset-images:ro" `
  foodscan-easyocr-baseline `
  --cases "/workspace/測試/量化測試/cases.json" `
  --image-root /dataset-images `
  --output /workspace/.artifacts/easyocr-gpu `
  --device cuda `
  --limit 5
```

Use `--device cpu` to run the identical reader without CUDA. Model weights are
downloaded and checksum-verified while the image is built; inference runs with
downloads disabled.
