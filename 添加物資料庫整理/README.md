# 食品添加物資料整理管線 (Food Additives Data Enrichment Pipeline)

本資料夾負責「FoodScanIoT」專案中 804 筆食品添加物安全資料（ADI、IARC 致癌分類、權威官方來源）的採集、校驗與導入工作。

為了提高資料嚴謹性並方便團隊（學弟妹）協作，專案已重構為以下 **4 階段 Pipeline 目錄結構**。所有主動開發與執行的腳本均以 `stepX_...` 的順序進行命名前置，確保執行流程一目了然。

---

## 📂 目錄結構與分工

```
添加物資料庫整理/
├── README.md                   ← 本導覽手冊
│
├── 01_data_sources/            ← 【步驟 1：原始資料與採集】
│   ├── raw/                    │ 存放原始的 TFDA 官方 XLSX 清單
│   ├── wiki_cache/             │ Wikipedia 備份快取 JSON
│   ├── harvested_links/        │ 自動採集到的 Wiki 外部連結對照表 (wiki_source_map.csv)
│   └── step1_wiki_harvest.py   │ 採集外部連結之 MediaWiki API 腳本
│
├── 02_identity_verification/   ← 【步驟 2：化學身分校驗與補強（學弟妹 A 任務）】
│   ├── step2a_verify_pubchem.py│ 188 筆 PubChem-only 身份驗證與對照腳本
│   └── step2b_direct_harvest.py│ 129 筆無維基條目之添加物直查 JECFA/FDA 腳本
│
├── 03_llm_ingestion/           ← 【步驟 3：AI 語意擷取與原文存檔（LLM 核心流程）】
│   ├── extract_prompt.txt      │ 給 Gemini 的結構化擷取 Prompt (引文綁定式)
│   ├── step3a_prep_inputs.py   │ 準備 LLM 輸入格式的預處理程式
│   ├── step3b_fetch_sources.py │ 純 HTTP 抓取官方一手原文之腳本
│   ├── step3c_run_gemini_extraction.py │ 執行批次 AI 擷取任務的主程式
│   ├── inputs/                 │ 待擷取 JSON 檔 (包含網頁原文 pages)
│   ├── outputs/                │ 擷取結果：results.csv、review.csv 異常清單、audit/ 任務留底
│   └── source_archive/         │ 抓取到的 JECFA/EFSA 原始 PDF 與純文字存檔
│
├── 04_db_validation/           ← 【步驟 4：暫存、校驗與交易同步（學弟妹 B 任務）】
│   ├── step4a_verify_rules.py  │ 確定性校驗閘門 (檢查引文、排除 weekly ADI、過濾 IARC 暴露途徑)
│   ├── step4b_promote_staging.py│ 建立暫存表、乾跑與 Transaction 寫入/回滾程式
│   ├── step4c_calculate_metrics.py│ 統計 Ingestion 成效的量化評估腳本
│   └── test_db.py              │ PostgreSQL 資料庫連線測試腳本
│
└── archive/                    ← 【歷史與備份封存區】
    ├── historical_runs/        │ 舊版執行數據備份
    └── legacy_scripts/         │ 舊版或實驗性輔助腳本
```

---

## 🚀 四階段工作流說明

### 步驟 1：多源連結採集 (`01_data_sources`)
* **目的**：獲取添加物在維基百科等平台的外部連結，建立初版網域白名單。
* **執行**：
  ```bash
  python 01_data_sources/step1_wiki_harvest.py
  ```

### 步驟 2：化學身分確認與直查補強 (`02_identity_verification`)
* **目的**：
  1. 針對 188 筆僅具 PubChem 來源者，透過 PubChem API 比對 CAS/INS 號與中文名是否相符，防止 ID 混淆。
  2. 針對 129 筆無維基百科條目者，跳過 Wiki，直接用 CAS 號查詢 JECFA / FDA 並補齊原始網址。
* **執行**：
  ```bash
  python 02_identity_verification/step2a_verify_pubchem.py
  python 02_identity_verification/step2b_direct_harvest.py
  ```

### 步驟 3：文獻原文下載與 AI 擷取 (`03_llm_ingestion`)
* **目的**：將採集到 URL 的文獻以 HTTP 下載保存，再由 Gemini 讀取原文提取 ADI 與 IARC 候選數據。
* **執行**：
  ```bash
  # 1. 準備輸入資料夾 inputs/
  python 03_llm_ingestion/step3a_prep_inputs.py
  # 2. 自動抓取一手官方原文並備份至 source_archive/
  python 03_llm_ingestion/step3b_fetch_sources.py
  # 3. 執行 AI 擷取並產出結果至 outputs/results.csv
  python 03_llm_ingestion/step3c_run_gemini_extraction.py
  ```

### 步驟 4：確定性校驗與安全寫入 (`04_db_validation`)
* **目的**：對 AI 的擷取結果進行確定性過濾，將來源分為 Tier 1 (官方) 與 Tier 2 (學術)，並剔除非口服的 IARC 致癌標記，最終透過 DB 交易安全寫入正式資料庫。
* **執行**：
  ```bash
  # 1. 跑 5+1 道校驗閘門，產出 review.csv 異常表
  python 04_db_validation/step4a_verify_rules.py
  # 2. 將 APPROVED 數據載入暫存表，經乾跑無誤後 Transaction 寫入 additives 表
  python 04_db_validation/step4b_promote_staging.py
  # 3. 產出 Ingestion 成效量化報表
  python 04_db_validation/step4c_calculate_metrics.py
  ```

---

## 👥 團隊 GitHub 協作建議

1. **請勿直接 Commit 到 `main` 分支**。
2. 讓學弟妹開闢新分支進行開發，例如：
   * `feature/pubchem-verify` (指派給負責步驟 2 的學弟)
   * `feature/db-staging` (指派給負責步驟 4 的學妹)
3. 程式碼完成後提交 Pull Request (PR)，由學長進行 **Code Review**，通過後才能合併至主分支，以維護程式碼品質與資料庫安全。
