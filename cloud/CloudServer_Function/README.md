# CloudServer_Function 規則管理規範 (SOP)

本目錄遵循「真實來源」(Single Source of Truth) 原則，將所有特殊族群的評估規則從 AI 自由發揮轉向「有據可查」的硬核規則庫。

## 核心流程 (Five-Stage Workflow)

### 1. 規劃 (Planning)
- **目標**: 定義特定族群的評估指標（如：糖尿病的糖分閾值、腎病的磷鉀限制）。
- **工具**: 於 `CloudServer_Function/<族群>/planning/` 下存儲來源清單與準則初稿。
- **輸出**: `source_manifest.json` (包含官方文件連結、法律依據編號)。

### 2. 爬取 (Crawling)
- **目標**: 批量抓取官方公告或指引文本。
- **工具**: Puppeteer/Scrapy 抓取內容，利用 LLM 輔助生成關鍵字擴充種子。
- **輸出**: `raw_data/` 存放原始抓取的文本或 HTML。

### 3. 清洗 (Cleaning)
- **目標**: 將雜亂的文本轉換為結構化規則。
- **工具**: Gemini Flash 提取關鍵邏輯（觸發成分、閾值、建議語、法規引用），並使用 Pandas 標準化。
- **輸出**: `processed_rules.json` (最終用於 RAG 索引的格式)。

### 4. 品質控管 (Quality Control)
- **目標**: 驗證規則準確性，確保符合 PRISMA 或臨床準則。
- **工具**: Jupyter Notebook (`qc_report.ipynb`) 進行抽檢記錄與指標計算。
- **輸出**: `verification_log.md`。

### 5. 記錄 (Documentation)
- **目標**: 完整追蹤變更與邏輯流。
- **工具**: README 與 Git Commit 記錄。
- **輸出**: 最終歸檔至 `registry.py` 或由 RAG 引擎加載。

## 規則結構範例 (Standard Schema)
每個族群的 `rules.json` 必須包含：
```json
{
  "rule_id": "族群代碼-序號",
  "category": "慢性病/老弱婦孺/過敏族群",
  "trigger": {
    "keywords": ["成分關鍵字"],
    "threshold": {"field": "sodium", "value": 400, "unit": "mg"}
  },
  "assessment": {
    "advice": "專業建議文字",
    "severity": "info/warning/alert"
  },
  "evidence": {
    "source": "文獻或法規名稱",
    "section": "條文或頁碼",
    "url": "官方連結"
  }
}
```
