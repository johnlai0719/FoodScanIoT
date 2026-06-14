# FoodScan 後端串接參考包

這個資料夾是給後端開發者 / AI 的參考包，包含前端 APP 對 API 的完整期望。

---

## 檔案說明

| 檔案 | 用途 |
|------|------|
| `API_SPEC.md` | **Cloud 規格**：Cloud `:3002/api/analyze` 完整欄位說明 |
| `FOG_API_SPEC.md` | **Fog 規格**：Fog `:3001/query` 特有行為、快取狀態、個人化比對說明 |
| `types.ts` | **資料契約**：TypeScript 介面定義，欄位名稱和型別必須完全對齊 |
| `mockResultData.ts` | **真實範例**：前端目前使用的兩筆 mock 資料，可直接對照輸出格式 |
| `scoring.ts` | **解析邏輯**：前端如何使用 `health_score` 和 `score_breakdown`，了解欄位會觸發哪些顯示行為 |

---

## 快速對照順序

1. 先看 `types.ts` — 確認所有欄位名稱和型別
2. 再看 `mockResultData.ts` — 對照真實結構範例
3. 有疑問再查 `API_SPEC.md` — 詳細說明每個欄位的邏輯
4. `score_breakdown` 格式不確定時看 `scoring.ts`

---

## 重要注意事項

- `barcode` 和 `label_images` 至少提供其一
- `ingredients_detail` 每項的 `isAdditive` 影響 APP 顯示紅色警示框
- `groupRisks[].riskLevel >= 3` 才會被標記為高風險
- `food_safety_events[].sources` 分三層：`official`、`news`、`social`
- 後端 IP 目前為 placeholder，串接時替換 `HomeScreen.tsx` 第 123 行
