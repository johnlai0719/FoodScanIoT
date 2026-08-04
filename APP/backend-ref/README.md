# FoodScan 後端串接參考包

這個資料夾是給後端開發者 / AI 的參考包，包含前端 APP 對 API 的完整期望。

---

## 檔案說明

| 檔案 | 用途 |
|------|------|
| `types.ts` | **資料契約**：已改為轉出 `../src/types.ts`（見下方說明），欄位定義請直接讀該檔 |
| `mockResultData.ts` | **真實範例**：前端目前使用的兩筆 mock 資料，可直接對照輸出格式 |
| `scoring.ts` | **解析邏輯**：前端如何使用 `health_score` 和 `score_breakdown`，了解欄位會觸發哪些顯示行為 |

> 本表原本還列了 `API_SPEC.md` 與 `FOG_API_SPEC.md`，這兩個檔案**在此資料夾中並不存在**，已從表中移除以免浪費查找時間。

## 契約的權威來源（2026-08-04 更新）

`types.ts` 原本是 `../src/types.ts` 的手抄副本，兩份已經漂移，現已改為 `export * from '../src/types'`，物理上不可能再不一致。

實際的權威來源與**會在違反時報錯**的強制點：

| 對象 | 產生處 | 強制處 |
|------|--------|--------|
| Cloud 回應欄位集合 | `server/module_d/response_builder.py` 的 `build_response()` | `tests/contract/test_cloud_response_contract.py`（欄位集合已凍結） |
| 族群風險詞彙 | `server/module_a/ingredient_matching.py` 的 `GROUP_ZH_TO_EN` | `tests/contract/test_group_vocabulary.py`（跨層比對 App 的 `CLOUD_GROUP_CODES`） |
| App 個人化行為 | `APP/src/utils/personalization.ts` | `APP/src/utils/__tests__/personalization.test.ts` |

改動任一邊而未同步另一邊，`pytest` 或 `npm test` 會直接轉紅。

> **注意**：個人化比對（過敏原、族群風險、慢性病閾值）已於 2026-08-04 由 Fog 移至 App 端本地執行，App 不再送出 `user_conditions`。Fog 端只剩格式正規化。

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
