# FoodScan Live Pipeline

本機展示儀表板：影像接收 → Google Vision → Region Routing → Gemini → 結果同步。

## 點選階段查看處理內容

左側五個階段可點選，也可用鍵盤 Tab／Enter 操作。選取後會保留選擇，定時進度更新不會切走正在查看的階段。

| 階段 | 說明與實際資料 |
|---|---|
| 影像接收 | 輸入照片、圖片尺寸及接收方式 |
| Google Vision | OCR 處理說明、青色字詞框與可展開的 OCR 原文 |
| Region Routing | 營養與其他密集文字的規則說明、區域框、裁圖與座標 |
| Gemini | 結構化抽取說明與實際回傳欄位（新 reader 事件提供） |
| 結果同步 | 後端結果摘要與 App 顯示回報狀態 |

階段操作說明與本次實際資料分開呈現。沒有執行的範例階段、未回報的舊紀錄與失敗狀態皆會標明，不會產生替代輸出。已存 c99 範例只含 Vision／規則分區，不附會先前另一個流程的 Gemini 結果。

## 啟動儀表板

從決賽專案根目錄執行：

```powershell
python tools/pipeline_dashboard.py
```

本機網址：`http://127.0.0.1:8787`。此服務僅接收進度，不會自行呼叫 Vision、Gemini 或切換原有服務。頁面可以查看已存的分區範例；範例明確標記，Gemini 與手機同步不會假裝完成。

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

