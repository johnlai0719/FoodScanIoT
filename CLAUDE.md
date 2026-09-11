# FoodScanIoT — 給 AI 助理與新進開發者的專案說明

食品標示分析系統。使用者掃條碼或拍標示照片，系統回傳添加物說明、Nutri-Score
健康評分、以及依個人健康條件產生的警示。

**這份檔案放的是「讀程式碼看不出來、或看了會誤解」的事**。純粹的結構說明請看
`README.md`；設計討論與決策紀錄在 Obsidian 的 `專案管理/`。

---

## 三層拓樸

```
APP (Expo / React Native，手機)
  │  POST /query          {barcode, label_images}
  ▼
FOG — Node.js 層  :3001   快取、驗證、轉發
  │  POST /query          {...原請求, cached_result?}
  ▼
FOG — Python 層   :3002   資料脫敏、格式正規化
  │  POST /api/analyze    （脫敏後的 payload）
  ▼
CLOUD (FastAPI)   :3003   Gemini 視覺辨識、添加物比對、Nutri-Score、AI 摘要
  ▼
PostgreSQL        :5432
```

Fog 的兩個行程由 PM2 管理（`fog/ecosystem.config.js`），部署在 Raspberry Pi 上。

**注意埠號有兩套**：`server/main.py` 獨立執行時預設 `CLOUD_PORT=3003`，但
`docker-compose.yml` 與 `server/.env.example` 用的是 **8000**。App 目前硬編碼打
`:3003`。改動前先確認目標環境用哪個。

---

## 目錄

| 路徑 | 內容 |
|---|---|
| `APP/` | 手機端。實際程式在 `APP/src/`，主畫面是 `screens/HomeScreen.tsx` |
| `fog/` | 邊緣節點。`server.ts`/`queryHandler.ts`/`cache.ts` 是 Node 層；`main.py` 是 Python 層；`transforms.py` 是純轉換函式 |
| `server/` | Cloud。已模組化：`module_a` 成分比對、`module_b` 計分與每日參考值、`module_c` 食安事件、`module_d` 組裝回應 |
| `shared/` | 跨層 TypeScript 型別。**App 不 import 它**（Metro bundler 的 projectRoot 是 `APP/`，跨出去會打包失敗），只有 `fog/*.ts` 在用 |
| `tests/contract/` | 跨層契約測試，CI 會跑 |
| `添加物資料庫整理/` | 添加物知識庫的建置管線 |

---

## 怎麼跑

```bash
# 契約測試（純函式，不需 DB / API key）
pytest

# App 測試與型別檢查
cd APP && npm test
cd APP && npx tsc --noEmit     # 注意：有 28 個既有錯誤，見下方

# 跑 App
cd APP && npx expo start

# 後端（DB + Cloud）
docker compose up -d
```

`server/.env` 需要 `GEMINI_API_KEY`、`TAVILY_API_KEY`、DB 連線資訊，
範本見 `server/.env.example`。

---

## 跨層契約：在哪裡、誰在強制

這個專案最容易出錯的地方是層與層之間的資料契約。以下每一條**改了就會讓 CI 紅**：

| 契約 | 產生處 | 強制處 |
|---|---|---|
| Cloud 回應的 22 個頂層欄位 | `server/module_d/response_builder.py` 的 `build_response()` | `tests/contract/test_cloud_response_contract.py` |
| 族群風險詞彙 | `server/module_a/ingredient_matching.py` 的 `GROUP_ZH_TO_EN` | `tests/contract/test_group_vocabulary.py`（跨層比對 `APP/src/constants/groupVocabulary.ts`） |
| 三層串接後欄位存活 | 上述兩者 ＋ `fog/transforms.py` | `tests/contract/test_layer_chain.py` |
| App 個人化行為 | `APP/src/utils/personalization.ts` | `APP/src/utils/__tests__/personalization.test.ts` |
| Fog 本機降階回應 | `fog/transforms.py` 的 `build_degraded_local_response()` | `tests/contract/test_degraded_local_contract.py`（跨層比對 `APP/src/types.ts` 的 `DegradedLocalResponse`） |

**設計原則：契約測試一律是純函式測試**，不啟動伺服器、不連資料庫、不需要 API
key。這樣它們才能在 CI 上每次 push 都跑。加新測試時請維持這個性質——需要外部
服務的測試會三天兩頭紅燈，最後大家就開始忽略它。

不採用 FastAPI 的 `response_model`：`/api/analyze` 有多種回應形狀，而
`response_model` 預設會**過濾掉未宣告的欄位**，可能靜默丟掉 App 需要的資料。

---

## 已知陷阱

**營養資料只在 `data.nutrition_facts`，頂層沒有。**
只有五個欄位（calories/protein/fat/sugar/sodium），基準是每 100g/100mL。
飽和脂肪不在裡面（存在於 `daily_reference.items[]`），所以高血脂的閾值警示做不了。
值可能是 `null`，**不可當成 0**——「沒有標示」與「含量為零」是兩件事。

**`groupRisks[].group` 只有 7 種英文值**：`pregnant`／`child`／`kidney_disease`／
`asthma`／`aspirin_allergy`／`pku`／`allergy`。不在 `GROUP_ZH_TO_EN` 內的中文族群
標籤（「高血壓患者」「血鐵沉著症患者」等）會被**靜默丟棄**。
所以高血壓與糖尿病的個人化**只能靠營養素閾值**，不能靠 groupRisks。

**「分析失敗」是 HTTP 200，不是 HTTP 錯誤。**
Cloud 回 `{status:"rejected"|"not_found"|"error", message}`，Fog 回
`{status:"degraded", message}`，四種都沒有 `health_score`。判定結果是否有效請看
**有沒有 `health_score`**，不要只看 `response.ok`。
Fog 另有一種 `{status:"degraded", degraded_mode:"local_ocr", ingredients_detail, …}`：
Cloud 連不上且無快取時的本機 OCR 部分結果，同樣**刻意沒有** `health_score`，
所以現行 App 會顯示它的 `message`。它不可再經過 `normalize_result()`，那會補上預設 75 分。

**Fog 本機降階依賴 `PPOCR_TEST/`，而它目前只在 `feat/vlcrop-pipeline` 分支。**
`fog/local_ocr.py` 啟動時找不到它就停用降階（`/health` 的 `local_ocr` 會顯示原因），
行為退回原本的 504。另外 `deploy-fog.yml` 會在 Pi 上 `git reset --hard origin/main`，
在 Pi 的 repo 目錄裡開發時，未 commit 的修改會被清掉。

**Node 與 Python 共用同一個 `fog_cache.db`**，但寫入語義不同（TTL 不同、
時機不同，Node 的 `INSERT OR REPLACE` 會覆蓋 Python 先寫入的那筆）。動快取邏輯
前先讀 `fog/cache.ts` 與 `fog/main.py` 兩邊。

**`tests/` 整個被 gitignore**，只有 `tests/contract/` 例外（`.gitignore` 用
`tests/*` ＋ `!tests/contract/`；git 無法在父目錄被排除後再納入子目錄，所以不能
只加否定規則）。在 `tests/` 下新增其他測試不會進版控。

**`APP/package-lock.json` 被根目錄的 `*.json` 規則擋掉**，所以 CI 只能用
`npm install` 而非 `npm ci`，每次安裝版本可能不同。

**`fog/cache.test.ts` 會刪掉正式快取**——它的 `afterEach` 對真實的
`fog_cache.db` 執行 `DELETE FROM cache`。在 Pi 上不要跑 `npm test`。

**兩個預設關閉的功能**：
- `server/main.py:124` 的 `SAFETY_EVENTS_ENABLED = False`——食安事件管線整個停用中，
  但資料庫裡有既有資料，讀文件容易以為它在線上運作。
- 語意比對（Vector RAG）預設關閉，環境變數 `VECTOR_RAG_ENABLED=1` 才啟用。
  關閉原因：261 個判定中 256 項靠精確比對即命中，且語意層命中時無出處可寫。

**App 硬編碼 Tailscale IP**（`HomeScreen.tsx` 內 `100.86.249.39:3001` 與
`100.119.217.100:3003`）。換環境要改程式碼。

**已知壞掉**：進階設定的「清除 Fog 快取」按鈕打 `POST :3001/cache/clear`，
但 Node 層只有 `DELETE /cache` 和 `DELETE /cache/:barcode`，該路由不存在 → 404。

**`npx tsc --noEmit` 目前有 28 個既有錯誤**（缺 `expo-image` 等套件、`@/` 路徑
別名未設定），與新改動無關。判斷有沒有引入新錯誤請比對**錯誤集合**而非數量。

---

## 慣例

**個人化只在 App 端本地執行。** 使用者的健康背景（族群、慢性病、過敏原）
**不會送往 Fog 或 Cloud**——App 的請求 body 只有 `{barcode, label_images}`。
`fog/transforms.py` 的 `mask_sensitive_data()` 保留剝除邏輯作為深度防禦，
以防舊版 App 仍送出。新增功能時請維持這個界線。

**文件不刪、只加註。** 被推翻的設計原文保留，在旁邊註明被什麼取代、日期為何。
Obsidian 的 `專案管理/文件與程式落差對照表` 記錄了文件與程式碼的已知落差，
是這個 vault 最有價值的東西之一。

**同一份知識不要放兩個地方。** 族群詞彙曾經散在四處各自維護，導致中英文比對
方向相反、六種族群警告從未觸發。現在單一來源在 Cloud，由契約測試強制。

---

## 相關文件

- `README.md` — 目錄結構與部署指令
- `APP/backend-ref/README.md` — 後端串接參考，含契約權威來源對照表
- Obsidian `專案管理/` — 設計討論、決策紀錄、學術文獻依據、給下一屆的交接說明
