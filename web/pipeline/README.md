# FoodScan Live Pipeline

本機展示儀表板：影像接收 → Fog 決策 → Google Vision → Region Routing → Gemini → 結果同步。

## 點選階段查看處理內容

左側六個階段可點選，也可用鍵盤 Tab／Enter 操作。選取後會保留選擇，定時進度更新不會切走正在查看的階段。

| 階段 | 說明與實際資料 |
|---|---|
| 影像接收 | 輸入照片、圖片尺寸及接收方式 |
| Fog 決策 | Node／Python 實際回報的 Hit、Miss、Bypass、排隊及補送事件 |
| Google Vision | OCR 處理說明、青色字詞框與可展開的 OCR 原文 |
| Region Routing | 每張照片一個文字外接大框的規則說明、區域框、裁圖與座標；歷史事件另標示歷史分區 |
| Gemini | 結構化抽取說明與實際回傳欄位（新 reader 事件提供） |
| 結果同步 | 後端結果摘要與 App 顯示回報狀態 |

階段操作說明與本次實際資料分開呈現。沒有執行的範例階段、未回報的舊紀錄與失敗狀態皆會標明，不會產生替代輸出。已存 c99 範例只含 Vision／規則分區，不附會先前另一個流程的 Gemini 結果。

## 啟動儀表板

### 本輪本地新增：Fog 情境觀測

畫面分開顯示「預定演示情境」（App 選擇）與「Fog 實際回報」（Fog 發送）。App 選擇 Cache Hit 不會讓畫面偽造命中；查不到快取仍顯示 Miss。純照片 `TEST` 不共用條碼快取，顯示 `EXCLUDED`；強制重辨識顯示 `BYPASS`，不冒充自然 Miss。

| Fog 實際事件 | 儀表板行為 |
|---|---|
| `cache_hit`／`cache_returned` | 顯示 HIT／HIT-STALE／HIT-RAW；未執行的 Vision、分區、Gemini 標示略過 |
| `cache_miss` | 顯示未找到可用快取，後續是否送雲端或排隊依下一個 Fog 事件決定 |
| `cache_bypass`／`cache_excluded` | 清楚區分手動略過快取及純照片不共用快取 |
| `cloud_unavailable` | 只說雲端不可用，尚不宣稱資料已保存 |
| `queued` | Fog SQLite 寫入成功後才回報，顯示任務編號並等待補送 |
| `replay_started`／`replay_retry` | 顯示開始補送或等待重試及實際嘗試次數 |
| `replay_completed` | 顯示補送完成；仍等 App 自己回報顯示結果，Fog 不代替手機確認 |
| `storage_failed`／`unavailable`／`replay_failed` | 顯示失敗，不標成已暫存或同步完成 |

事件以 `request_id` 關聯；相同待辦重複提交會保留原佇列編號並將原任務補送事件對應回新提交。每筆保留最近 40 個 Fog 事件。事件中的時間為儀表板接收時間，不用跨機器時間戳計算延遲。

Fog 必須同時更新 Node 與 Python：新增 `pipelineEvents.ts`、`fog_events.py`，並更新 `server.ts`、`queryHandler.ts`、`main.py`；執行 `npm run build`。兩個 Fog 服務都設定 `PIPELINE_DASHBOARD_URL=http://儀表板的Tailnet名稱:8787`，重啟服務後才會回報。回報為可選、盡力傳送；Python 使用有界背景佇列，Node 不等待觀測器，儀表板離線不改變原分析／快取／補送結果。

儀表板後端也需要重啟以載入新增事件格式；僅刷新網頁不會更新 FastAPI schema。Cloud／reader 仍可沿用原本的事件回報設定。App 的 `X-Demo-Scenario` 只傳遞演示意圖，不控制 Fog 命中／斷線，不需把任何金鑰放入 App。

依使用者決定，本輪**僅完成本地版本，未更新 Pi，未重啟正式儀表板**。目前執行中的後端仍不接受新增 Fog 事件，請部署以上兩端後再連動驗收。

### 合成預覽（不接 Pi，也不呼叫付費 API）

```powershell
python tools/pipeline_dashboard_preview.py
```

預設只監聽 `127.0.0.1:8788`。可分別開啟 `/preview/hit`、`/preview/miss`、`/preview/bypass`、`/preview/queue`、`/preview/replay`。畫面清楚標示合成演示，資料與正式儀表板記憶體隔離；Search API 在此預覽固定拒絕。這些畫面驗證不等同 Pi／App 端到端驗收。

從決賽專案根目錄執行：

```powershell
python tools/pipeline_dashboard.py
```

本機網址：`http://127.0.0.1:8787`。此服務僅接收進度，不會自行呼叫 Vision、Gemini 或切換原有服務。頁面可以查看已存的分區範例；範例明確標記，Gemini 與手機同步不會假裝完成。

### 目前 Tailscale 遠端入口（2026-10-28）

`http://johnlai-pc.tail879308.ts.net:8787/`，僅 Tailnet 可存取，使用 Tailscale Serve 轉接現有 `127.0.0.1:8787`，因此保留目前記憶體中的進度。請使用此 MagicDNS 網址；本次設定直接使用 Tailscale IP 的 Host 會回 404。

Serve 的 `/api/grounding/search` 另以文字回覆覆蓋，遠端不轉送付費 Search 實驗；本機原端點仍維持原限制。撤除此入口可執行 `tailscale serve --http=8787 off`，不需關掉本機儀表板。

App 的 `.env.local` 已保存 `EXPO_PUBLIC_PIPELINE_DASHBOARD_URL` 為上述網址。但本輪重啟 App 開發服務遭工具政策拒絕，現有 iOS bundle 尚未包含此設定；需日後重啟 Expo 才能取得手機同步回報。遠端儀表板 HTTP 200 與既有 Cloud／reader 事件已確認正常，未偽造手機接收確認。

## 接既有 FoodScan App

1. 在同一可信任展示網路上，儀表板以 `PIPELINE_HOST=0.0.0.0` 啟動，取得電腦可供手機連線的 IP。此本機觀測器未設登入，不應公開到網際網路。
2. App 啟動環境設定 `EXPO_PUBLIC_PIPELINE_DASHBOARD_URL=http://電腦IP:8787`，重啟 Expo。App 本身沿用原有掃描端點；此設定不是分析 API，也不是金鑰。
3. 決賽 Cloud 與新 reader 的環境設定 `PIPELINE_DASHBOARD_URL=http://儀表板位址:8787`。容器需要用能到達主機的位址，例如 `host.docker.internal`。
4. 新 reader 另設 `GOOGLE_APPLICATION_CREDENTIALS` 指向現有 Vision 憑證，`GEMINI_API_KEY` 設定於本機環境；不放入 App。以 `python tools/reader_vision_competition.py` 啟動，預設 `127.0.0.1:8181`。
5. 決賽 Cloud 的 `VISION_BACKEND=vlcrop`（此值在現有程式指走 reader 的傳輸介面），`READER_URL` 指向新 reader 的 8181；如使用容器，reader 須綁可達介面。`API_SHARED_SECRET` 需與 Cloud 一致。
6. App、Fog、Cloud 仍需完成獨立決賽環境配置，見外層 `比賽/獨立開發環境.md`。此次沒有啟動 Cloud、修改研究環境、搬移資料庫或替手機安裝 App。

每次 App 送出掃描會沿用 `X-Request-Id`；Cloud 將它傳給 reader。reader 在實際階段開始／結束時回報事件，並提供第一張原圖、分區與裁圖。多圖仍會全部處理，但儀表板影像預覽先顯示第一張。

展示完整五階段時，需使用新商品或開啟 App 既有的略過快取設定；快取命中不會重跑 Vision／Gemini。

Cloud 結果備妥只標示等待手機。App 收到成功結果後，在結果 state commit 後的畫面更新回呼中回報確認，儀表板才顯示同步完成。這是程式顯示回呼，不代表人已閱讀。快取可能略過 Vision/Gemini；未回報階段不會假裝執行完成。

## 驗證範圍

已提供 API／狀態測試與本機畫面，並以 c99 圖片單張實際跑通 Vision → 規則分區 → Gemini，儀表板收到真實進度事件；此驗證沒有呼叫 Cloud 分析／計分與手機 App。原始結構化結果保存於外層 `比賽/共用素材/儀表板驗證/reader_result.json`。

真實手機、Fog、Cloud、資料庫與新 reader 的完整連線，需要在決賽環境設定後實機驗證。新 reader 的裁圖＋原圖 Gemini 輸入是新流程，不沿用先前 EasyOCR 的效能結論。

App 修改檔已通過 TypeScript 語法轉譯檢查；獨立 checkout 尚未安裝 App 依賴。嘗試借用既有 checkout 的依賴做完整型別檢查時，出現 React 型別、expo-image、expo-router 等依賴解析錯誤，不能視為通過完整 App 型別檢查或建置。需在獨立 App 安裝相符依賴後重新檢查。

進度事件只傳商品影像與最小結果摘要，不傳 App 使用者健康設定。服務只在記憶體保留最近八筆請求，重啟即清除。
# Google Search Grounding 獨立測試區

2026-10-28 決策：Search 暫不採用於主展示。保留測試程式，但介面預設收合於「實驗工具封存」，不接掃描／評分。Tesseract亦維持獨立實驗，主線及驗證邊界見 [比賽交接文件](../../competition/README.md)。

## 廠商事件查詢實驗（2026-10-28）

預設模式改為廠商事件查詢：廠商／品牌至少填一項，商品名稱與補充方向選填。目的為查找廠商相關公開食安事件報導，再判斷與輸入商品的關係。允許新聞作為主要線索，官方資料用於補強查證，不限定只能命中官方公告。

POST 除原本的 `query` 外，可傳 `mode: "vendor_events"`、`manufacturer`、`brand`、`product`。後端組合檢索要求，包含法人辨識、事件日期與報導日期分離、消息狀態、後續澄清、同案轉載與商品關聯；回應附查詢時間、查詢對象與 `review_status: "unreviewed"`。原 `mode: "general"` 保留相容。

上述欄位整理與關聯判斷目前由提示詞要求，以文字回答呈現，**尚未具備逐事件結構驗證、人工核對、完整性或重複查詢穩定性驗收**。Grounding 引用只代表取得來源對應，不代表事件已證實、來源彼此獨立或確實適用本商品。正式 Module C 仍停用，不寫入事件庫或評分，不自動接入 App。

儀表板下方提供搜尋接地測試，尚未併入五階段掃描或 FoodScan App。啟動頁面只讀取設定，按「執行搜尋測試」才呼叫 API。回答、搜尋字串、網頁來源與回答段落對應皆取自實際回應；未取得來源對應時不標示接地成功。Google 搜尋建議以允許清單保留原始樣式與連結，在 Shadow DOM 中顯示，不執行腳本或事件屬性。

- `GET /api/grounding/config`：模型名稱與金鑰是否存在，不回傳金鑰。
- `POST /api/grounding/search`：`{"query":"問題"}`；僅允許本機發起，同時最多一筆，60 秒 API 逾時，不自動重試。
- 模型預設 `gemini-2.5-flash`，可用 `GROUNDING_MODEL` 指定支援 Google Search 的模型。
- 金鑰讀取 `GEMINI_API_KEY` 環境變數，或 checkout `.env`、工作區根目錄 `.env`；可用 `GROUNDING_ENV_FILE` 指定檔案。金鑰只在後端使用。
- 不寫入掃描資料庫、不啟用既有食安監控。每次測試依 Google API 帳戶方案計費。

官方 API：[Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search)。



## 統一文字區塊與圖片放大（2026-10-28）

Region Routing 現在合併通用文字密集大區塊，不先分營養／成分等欄位。營養欄位仍由 Gemini 從原圖與裁圖抽取。單張 c99 的離線比較由 3 框減為 2 框，舊營養區 24 個 OCR 段落被同一新框涵蓋；尚無 Gemini 新舊準確度比較。

點擊原圖可放大當下的 OCR／分區標註，點擊裁圖可單獨放大；Esc、關閉按鈕或點背景可關閉。預覽開啟時保留當下圖片，背景仍可接收新事件。原圖也支援鍵盤 Enter／空白鍵開啟。

既有已完成紀錄仍呈現當時實際分區。新的 reader 流程需重啟 reader；已存範例 API 會以目前規則重新計算裁圖，需重啟 dashboard 載入後端修改。

## 2026-10-29 現行單框流程

Reader 已改為 `vision_text_envelope_v1`，每張照片只送一張外接大框裁圖（或完整影像 fallback）給 Gemini。重新整理頁面即可載入新階段說明，無須為新事件重啟儀表板。第一張照片顯示一個框及一張輸入預覽；多張照片的總輸入張數由 Reader 事件提供。

沒有新策略標記的舊分區事件及「已存分區範例」屬歷史流程，頁面會明確標示。單張 Reader 測試不會產生手機收件確認，結果同步仍須由 App 真實回報。

## 2026-10-29 白底 Demo 與實際 Pipeline

移除頁首與頁尾標語，改用白底、深色文字及藍色狀態提示。上方以「目前狀況／本次 Pipeline」取代預定演示設定，依實際 Fog 事件與 Cloud 階段回報顯示快取命中、雲端分析、離線排隊、補送或失敗。`demo_scenario` 不作為已發生路徑的判定依據；只有 Cloud 事件時不推斷 Fog 快取決策。CSS／JS URL 加入版本參數以更新瀏覽器快取。

驗證：13 組狀態資料檢查通過，含缺少 Fog 事件、設定與實際路徑不一致、離線與補送；瀏覽器確認白底、目前真實 Reader 紀錄顯示雲端路徑、階段切換與裁圖放大／關閉。此輪未對 Pi 或手機另做端到端掃描測試。

## OCR 品質檢查

Vision 後先檢查所有照片合計的文字／數字字元數；0～2 字元時顯示「文字不足，請重拍」，Pipeline 顯示「Vision → OCR 品質檢查 → 重拍（未呼叫 Gemini）」，Gemini 顯示略過。此為保守的最低文字量檢查，不是食品分類或文字完整性判定。重拍提示仍須由 App 實際收到，不能把儀表板手機預覽當作手機確認。

## 雲端連線模擬控制

新增控制面板，只在 Fog 回報已套用後顯示故障已生效。快取保持真實查詢；首次成功入列後可倒數5秒解除模擬，也可手動恢復。解除後由 Fog 原背景 worker 補送，不保證立即收到結果。需新版後端、新版 Fog、相同專用金鑰及 Fog opt-in 設定，詳見 `fog/DEMO_CONTROL.md`。前端讀不到新 API 時會停用按鈕並提示重啟；不會把按鈕設定轉成虛假的排隊或完成事件。


## 2026-10-31 展演任務與可還原重設

新增「下一筆走 Miss」、取消預備、指定條碼準備、任務／圖片清單、封存本輪新增資料與逐筆還原。新功能說明與部署步驟見 `competition/展演任務與重設.md`。下一筆設定需新版 Fog Node 在讀快取前領取指令；舊 Pi 不會因網頁更新自動支援。套用確認與實際 Miss 事件分別呈現，且不會代替 App 回報收件。

僅清理登記後的本輪新增商品與圖片，原有商品保留；已封存資料保留於 `.artifacts`，不永久刪除。清理需確認 Cloud 無進行中請求、Fog 無 pending/processing 任務。未登記的歷史條碼與圖片不納入。

本地隔離測試通過準備、單次領取與 ACK、既有資料保留、封存還原、搬移失敗回滾及還原衝突；瀏覽器驗證預備／取消／批次封存／還原。Fog 建置通過，三組 Jest 測試共 11 項通過；完整 Jest 的 cache.test.ts 因本機 better-sqlite3 原生 binding 缺漏無法載入，未視為完整測試通過。尚未以 Pi／手機完成此功能端到端驗收。
