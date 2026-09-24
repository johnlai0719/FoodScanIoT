# FoodScanIoT — 食品標示分析系統

拍下食品包裝的標示（或掃條碼），系統讀出成分與營養標示，對照衛福部食品添加物正面表列，
回傳添加物說明、Nutri-Score 健康評分，以及依使用者健康條件產生的提醒。

採 **App → Fog → Cloud** 三層架構：使用者的健康條件只留在手機本機，不會送上伺服器。

---

## 資料夾導覽

### 系統本體

| 資料夾 | 內容 |
|---|---|
| `APP/` | 手機 App（Expo／React Native）。主畫面在 `APP/src/screens/HomeScreen.tsx`，後端位址在 `APP/src/constants/endpoints.ts` |
| `fog/` | 邊緣節點（Node.js 層 :3001 ＋ Python 層 :3002）：快取、驗證、資料脫敏、轉發；Cloud 連不上時提供本機 OCR 降階結果 |
| `server/` | Cloud（FastAPI :3003）。`module_a` 成分與添加物比對、`module_b` Nutri-Score 與每日參考值、`module_c` 食安事件（目前停用）、`module_d` 組裝回應 |
| `reader/` | 視覺辨識服務（:8180，需 GPU，跑在主機上）：PP-OCR 定位 → 裁切 → HunyuanOCR 讀字 → Qwen 整理欄位 |
| `web/` | 添加物開放查詢平台與管理後台（React＋Vite） |
| `tests/contract/` | 跨層契約測試（純函式、不需資料庫或 API 金鑰），CI 每次 push 都跑 |

### 研究與評估

| 資料夾 | 內容 |
|---|---|
| `測試/量化測試/` | 辨識準確度的量化測試集：案例定義 `cases.json`、人工正解 `ground_truth/`、評估腳本與歷次結果。照片不在 git 內（見該資料夾 README） |
| `PPOCR_TEST/` | 辨識管線的研究程式：讀取器比較、裁切、成分切分、營養表解析等實驗。`reader/` 與 `fog/` 的本機 OCR 會引用其中的模組，**不可任意搬動或改名** |
| `tools/` | 撰寫成果報告用的工具：數字溯源稽核、事實基底匯出、證據附錄匯出 |

### 資料建置

| 資料夾 | 內容 |
|---|---|
| `添加物資料庫整理/` | 804 筆食品添加物知識庫的建置管線（TFDA 官方資料 → 身分確認 → 引文綁定擷取 → 確定性驗證後寫入），`archive/` 為已停用的舊腳本 |
| `design/` | 架構圖與 App 介面設計稿的共用樣式 |

---

## 快速開始

```bash
# 契約測試（不需資料庫、不需 API 金鑰）
pytest

# 資料庫＋Cloud
cp server/.env.example server/.env      # 填入自己的資料庫連線與 API 金鑰
docker compose up -d
cd server && python db_init.py          # 建表並匯入添加物知識庫

# 視覺辨識服務（需 GPU）
python reader/start_models.py
python reader/service.py

# App
cd APP && npm install && npx expo start
```

`db_init.py` 會建立一個**僅供本機使用**的預設管理員帳號（`admin`／`admin123`），部署到本機以外之前務必修改。

`.env` 已被 gitignore，每位開發者自行保管，**不可提交**。本 repo 不附資料庫備份。

---

## 延伸閱讀

- **`CLAUDE.md`**：讀程式碼看不出來、或看了會誤解的事——拓樸細節、跨層契約與負責強制的測試、已知陷阱、延遲與 VRAM 的關係。**改程式前先讀這份。**
- `測試/量化測試/README.md`：測試集的版本規則與凍結原則（不同主版本的數字不可相比）
- `PPOCR_TEST/README.md`：辨識層各項實驗的紀錄與結論
- 設計討論與決策紀錄維護於 Obsidian，需要時向維護者索取

## 技術組成

App：React Native（Expo）、NativeWind｜Fog：Node.js（Express）＋ Python（FastAPI）、SQLite｜
Cloud：FastAPI、PostgreSQL、SQLAlchemy｜辨識：PaddleOCR、HunyuanOCR、Qwen3.5-2B（llama.cpp）；Gemini 2.5 Flash 保留為對照
