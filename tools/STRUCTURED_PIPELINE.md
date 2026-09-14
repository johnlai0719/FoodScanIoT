# Evidence-bound structured pipeline

Exploratory non-Chinese backend pipeline:

1. EasyOCR (NAVER/Clova CRAFT detector + CRNN recognizer, Apache-2.0) supplies text, confidence, and coordinates.
2. The existing CKIP Lab (Academia Sinica) BERT line classifier assigns one of eleven food-label
   field classes using previous/current/next line context.
3. Deterministic field parsers copy verbatim OCR evidence, parse nutrition values, and
   split ingredients without breaking nested formulations.
4. Pydantic validates types and rejects unsupported text spans.

No generative model assembles the JSON. Missing or unverified evidence remains `null`.

The current reusable BERT checkpoint was trained on weak labels from the larger
project evaluation corpus. It is valid for an integration smoke test, but results
on overlapping cases must not be reported as an unbiased accuracy estimate.

## Build

```powershell
docker build -f tools/structured.Dockerfile -t foodscan-structured-pipeline .
```

## Smoke test (4 cases)

```powershell
docker run --rm --gpus all `
  -v "D:\FoodScanIot\_worktrees\adi-multimodal-compliance:/workspace" `
  -v "D:\FoodScanIot\_worktrees\adi-multimodal-compliance\tools\structured_pipeline.py:/opt/foodscan/structured_pipeline.py" `
  -v "D:\FoodScanIot\APP-sync-server\PPOCR_TEST\out\linecls_model:/model:ro" `
  foodscan-structured-pipeline `
  --ocr-dir /workspace/.artifacts/easyocr-gpu-all `
  --model-dir /model `
  --output /workspace/.artifacts/structured-easyocr-bert-smoke `
  --case c01 `
  --case c02 `
  --case c03 `
  --case c05
```

## Full run (48 cases)

```powershell
docker run --rm --gpus all `
  -v "D:\FoodScanIot\_worktrees\adi-multimodal-compliance:/workspace" `
  -v "D:\FoodScanIot\_worktrees\adi-multimodal-compliance\tools\structured_pipeline.py:/opt/foodscan/structured_pipeline.py" `
  -v "D:\FoodScanIot\APP-sync-server\PPOCR_TEST\out\linecls_model:/model:ro" `
  foodscan-structured-pipeline `
  --ocr-dir /workspace/.artifacts/easyocr-gpu-all `
  --model-dir /model `
  --output /workspace/.artifacts/structured-easyocr-bert-all
```

## Content Review & Verification Fixes

During the initial 4-case content review, four real-world assembly defects were detected and fixed:
1. **Ingredients delimiter & whitespace recovery**: EasyOCR frequently transcribes `、` as backtick `` ` `` or drops punctuation between Chinese ingredient names. `split_ingredients` treats `` ` `` and top-level inter-CJK whitespace as delimiters while preserving bracketed formulas like `調味劑(L-麩酸鈉、5'-次黃嘌呤核苷磷酸二鈉)`.
2. **Manufacturer boilerplate rejection**: Packaging warning clauses (e.g. `芝麻的產品於同一工廠生產`) matched the `工廠` suffix. The parser explicitly filters out clauses containing `同一工廠`, `生產線`, or `過敏原`.
3. **Product name full-span preference**: Strips printed anchor prefixes (such as `品名：` or `名：`) and prioritizes Traditional Chinese candidates over English brand slogans (`純喫茶香橙綠茶` over `Orange Green Tea`).
4. **Fail-closed nutrition validation**: In Taiwanese mandatory food labeling, `calories` (熱量) is the foundational row of a nutrition table. When EasyOCR yields fragmented or misaligned text (such as scattered caffeine numbers or milk protein percentages), the parser rejects the table and leaves all nutrition values as `null` rather than propagating fabricated or shifted nutrients.

## Integration Evaluation (48 frozen cases)

- **Total cases requested**: 48
- **Elapsed time**: 4.533s (GPU batch inference)
- **Accepted as food label**: 33 / 48 (68.8%)
- **Rejected (non-food / unreadable OCR)**: 15 / 48 (31.2%)
- **Field Coverage**:
  - `name`: 29 / 48 (60.4%)
  - `manufacturer`: 16 / 48 (33.3%)
  - `allergy_warning`: 19 / 48 (39.6%)
  - `ingredients_list`: 33 / 48 (68.8%), avg 5.6 items/case
  - `nutrition` (valid table): 6 / 48 (12.5%)
  - Schema / Verbatim Warnings: 0

