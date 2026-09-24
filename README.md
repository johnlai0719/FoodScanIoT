# FoodScanIoT — 食品標示分析系統

拍下食品包裝的標示（或掃條碼），系統讀出成分與營養標示，對照衛福部食品添加物正面表列，
回傳添加物說明、Nutri-Score 健康評分，以及依使用者健康條件產生的提醒。

採 **App → Fog → Cloud** 三層架構：使用者的健康條件只留在手機本機，不會送上伺服器。

```
APP/      手機 App（Expo／React Native）
fog/      邊緣節點：快取、驗證、資料脫敏、轉發；Cloud 連不上時提供本機 OCR 降階結果
cloud/    雲端：分析 API、視覺辨識、添加物知識庫、管理後台
```

## cloud/ 裡面

| 路徑 | 內容 |
|---|---|
| `main.py`、`module_a`～`module_d` | 分析 API（FastAPI，對外 :3003）：成分與添加物比對、Nutri-Score 與每日參考值、食安事件（目前停用）、組裝回應 |
| `reader/` | 視覺辨識服務（:8180）：PP-OCR 定位 → 裁切 → HunyuanOCR 讀字 → Qwen 整理欄位。**需要 GPU，跑在主機上，不在容器內** |
| `vision/` | 辨識模組（成分切分、營養表解析、裁切等），`reader/` 與 Fog 的本機 OCR 共用 |
| `seed_data/` | 添加物知識庫（804 筆）與廠商清單的初始資料 |
| `web/` | 添加物開放查詢平台與管理後台（React＋Vite） |
| `tests/contract/` | 跨層契約測試（純函式、不需資料庫或 API 金鑰），CI 每次 push 都跑 |

## 快速開始

```bash
# 契約測試（不需資料庫、不需 API 金鑰）
pytest

# 資料庫＋Cloud
cp cloud/.env.example cloud/.env        # 填入自己的資料庫連線與 API 金鑰
docker compose up -d
cd cloud && python db_init.py           # 建表並匯入添加物知識庫

# 視覺辨識服務（需 GPU）
python cloud/reader/start_models.py
python cloud/reader/service.py

# App
cd APP && npm install && npx expo start
```

`db_init.py` 會建立一個**僅供本機使用**的預設管理員帳號（`admin`／`admin123`），部署到本機以外之前務必修改。

`.env` 已被 gitignore，每位開發者自行保管，**不可提交**。本 repo 不附資料庫備份。

## 延伸閱讀

- **`CLAUDE.md`**：讀程式碼看不出來、或看了會誤解的事——拓樸細節、跨層契約與負責強制的測試、已知陷阱、延遲與 VRAM 的關係。**改程式前先讀這份。**
- 研究與評估（辨識實驗、量化測試集、添加物知識庫建置管線）在另一個私人 repo `FoodScanIoT-research`，需要時向維護者索取權限。
- 設計討論與決策紀錄維護於 Obsidian。

## 技術組成

App：React Native（Expo）、NativeWind｜Fog：Node.js（Express）＋ Python（FastAPI）、SQLite｜
Cloud：FastAPI、PostgreSQL、SQLAlchemy｜辨識：PaddleOCR、HunyuanOCR、Qwen3.5-2B（llama.cpp）；Gemini 2.5 Flash 保留為對照
