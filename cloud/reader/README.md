# Reader 本機模型與記憶體管理

從 FoodScanIoT 根目錄啟動：

```powershell
python cloud/reader/service.py
```

reader 預設自行啟動 HunyuanOCR（GPU）與 Qwen（CPU），暖機後 `/health` 才會就緒。停止 reader 時會終止並等待自己啟動的模型行程。

**升級前先停止舊的 `reader/start_models.py` 啟動器與 reader。** 新版不接管或停止既有外部行程；發現模型埠已占用時，health 會回報原因。只用一個 reader worker，勿使用多個 uvicorn workers 或 reload 模式載入多份模型。

`cloud/reader/start_models.py` 保留供獨立實驗與 `--check`。如果刻意使用外部啟動器，可設定 `READER_MANAGE_MODELS=0`，但這會停用模型卸載／載回管理。

## 備援流程

同一時間只執行一筆推論。Hunyuan 沒定位到營養區時，先停止它並等待行程退出，再執行 PPStructure。備援完成（包括例外路徑）會清理閒置 Paddle GPU 快取，然後載回 Hunyuan 並等待就緒。

如果卸載失敗，不啟動備援；如果載回失敗，請求回 503，下一筆請求會重新嘗試啟動。管理器只使用自己保留的 Popen handle，不依行程名稱批次終止其他程式。

## 輸入與佇列預設上限

| 環境變數 | 預設 |
|---|---:|
| READER_REQUEST_CAPACITY | 2（處理中與等待中合計） |
| READER_MAX_BODY_BYTES | 16777216（16MiB，含 Base64 與 JSON） |
| READER_MAX_IMAGES | 4 |
| READER_MAX_IMAGE_BYTES | 6291456（單張 6MiB） |
| READER_MAX_TOTAL_IMAGE_BYTES | 12582912（合計 12MiB） |
| READER_MAX_IMAGE_PIXELS | 5000000 |
| READER_MAX_TOTAL_PIXELS | 12000000 |
| READER_MAX_IMAGE_SIDE | 4096 |
| READER_MODEL_START_TIMEOUT | 180 秒 |

圖片只接受靜態 JPEG/PNG/WEBP；在 RGB 解碼前檢查尺寸。上限包含 App 常見的 1920×2560 直式圖，但不代表此範圍內每種輸入都已驗證品質或記憶體。伺服器不自動縮圖。超出限制回 413，請先經 App 壓縮；無效編碼回 400、不支援格式回 415。超過請求容量回 503 與 Retry-After。

模型 context 超限、輸出 length/退化、定位區空文字，以及 PPStructure/Qwen 失敗，會回 502，不繼續組裝正常分析結果。區域本來就沒被定位到，與已定位卻辨識失敗，採不同處理。

## 驗證

```powershell
python -m pytest cloud/tests/contract/test_reader_memory_management.py -q
```

測試涵蓋外部行程保護、停止／恢復順序、啟動與載回失敗、請求容量、chunked body 上限、圖片尺寸及辨識異常。純測試不載入 GPU 模型。

GPU 診斷工具與原始資料保留在 FoodScanIoT-research 研究庫及本機，正式產品庫只保留執行時模組與純契約測試。

### 2026-10-18 移植前研究工作區驗證摘要

移植至 FoodScanIoT 的 `cloud/reader`、`cloud/vision` 後，正式產品庫的 338 項契約測試通過、無跳過。研究工具、測試集與原始診斷輸出保留於研究工作區。

RTX 4060 8GB、32GB RAM，c107/c11/c140 各兩輪，Pillow 模擬 App 寬度 1920、JPEG 品質 85（非 Expo 原生編碼器的逐位元重現）：六次請求完成、四次實際執行備援；六次營養輸出與常駐比較組一致。取樣 GPU 高峰 7385MiB，驅動回報最低可用 573MiB；前次常駐比較組為 7886MiB／72MiB。背景程式未固定，不能推導固定節省幅度或長時間穩定性。

完整本機契約測試為 359 passed、1 skipped（既有 guard-set 照片缺失）。輸入驗證實測：原始大圖 413、無效 Base64 400、空清單 413。測試後自有模型已退出。原始紀錄保留於本機 `_memory_probe_production/`，不提交生成的圖片、行程紀錄或辨識中間輸出。
