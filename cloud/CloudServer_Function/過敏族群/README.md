# 過敏族群規則庫 (Allergy Rules Database)

本目錄負責管理 11 大過敏原及其衍生添加物的識別規則。

## 執行現況
- **Stage 1: Planning** - 已完成，定義添加物對應目標。
- **Stage 2: Crawling** - 已完成，抓取正確法規 L0040124。
- **Stage 3: Cleaning** - 已完成，規則已結構化並加入 **EFSA/FDA 科學證據**。
- **Stage 4: Quality Control** - 已完成，通過隱性風險識別測試。
- **Stage 5: Documentation** - 本文件即為歸檔記錄。

## 核心規則邏輯
除了辨識包裝上的醒語外，本系統具備「隱性風險辨識」功能：
- **案例**：成分表含「酪蛋白」但無牛奶標示。
- **判斷**：觸發 `AL-004` (牛奶過敏規則)。
- **依據**：US FDA FALCPA 與 WHO Codex 認定酪蛋白為主要致敏蛋白質。

## 檔案說明
- `planning/source_manifest.json`: 資料來源清冊。
- `raw_data/L0040124_raw.txt`: 原始法規文本。
- `processed/processed_rules.json`: 結構化規則庫（供 RuleEngine 使用）。
- `qc/verification_log.md`: 測試驗證日誌。
