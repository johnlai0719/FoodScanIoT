# Fog 情境觀測與展示

2026-10-28，本地版本；使用者選擇稍後自行部署 Pi。

## 畫面如何判定情境

App 以 `X-Demo-Scenario` 傳遞演示意圖：`miss`（首次掃描）、`cache`（重複掃描）、`queue`（離線補送）、`cloud`（略過讀快取）。它不強制任何命中狀態，也不切斷 Cloud。

Fog 另外回報實際決策。儀表板依相同 `request_id` 合併 App、Fog 與 Cloud 事件，顯示「預定演示」與「實際發生」；左側新增可點選的 Fog 階段，能看兩層來源、佇列編號與補送次數。

| 發生什麼 | 顯示與後續 |
|---|---|
| Hit | 顯示快取類型，略過未執行的 Vision／分區／Gemini，等待 App 顯示回報 |
| Miss | 顯示未找到可用快取；若能連雲端，接續實際辨識階段 |
| Bypass | 顯示依設定略過讀快取，不冒充自然 Miss |
| TEST 純照片 | 顯示不共用條碼快取，不冒充查無快取 |
| 雲端不可用 | 尚不宣稱資料已保存；等 SQLite 寫入後才顯示已排隊 |
| 排隊／補送 | 顯示任務編號、等待／重試／正在補送及嘗試次數 |
| 補送完成 | 顯示結果可取得，仍須 App 自己回報才能標記手機接收 |

事件資料範例（新增的觀測通道，不變更食品分析回應契約）：

```json
{
  "request_id": "app-example",
  "stage": "fog",
  "status": "active",
  "sender": "fog",
  "fog": { "event": "cache_miss", "layer": "python", "cache_status": "MISS" },
  "payload": {}
}
```

## 部署時需一起更新

1. 儀表板：更新 `tools/pipeline_dashboard.py` 與 `web/pipeline/`，重啟後端載入新 schema。原 Cloud／reader 的回報 URL 不需要因此更動。
2. Fog Node：更新 `server.ts`、`queryHandler.ts`、新增 `pipelineEvents.ts`，執行 `npm run build`。
3. Fog Python：更新 `main.py`、新增 `fog_events.py`。保留原 DB、快取、佇列與憑證，不需要清空資料。
4. 兩個 Fog 服務均設定 `PIPELINE_DASHBOARD_URL` 為可從 Pi 存取的 Tailnet 儀表板網址，再重啟服務。從 systemd 啟動時須確認環境變數確實載入兩個服務。
5. App 載入本地新版本，若需手機同步確認，亦須讓 Expo 載入 `.env.local` 的觀測器網址。

觀測器不回傳控制命令給 Fog。Python 以有界背景佇列回報，Node 不等待回報；無法連儀表板時保留原來的分析／排隊行為。無回報時只顯示尚未收到，不自行推測命中或完成。

## 本地合成預覽

```powershell
python tools/pipeline_dashboard_preview.py
```

以 `127.0.0.1:8788/preview/` 開啟 `hit`、`miss`、`bypass`、`queue` 或 `replay`。明確標為合成演示，不連 Pi／Cloud，不呼叫付費 Search、Vision 或 Gemini，不產生手機接收回報。截圖保存於 `比賽/共用素材/Fog儀表板情境/20261028/`。

驗證：Python 389 passed、1 deselected（既有 guard 實拍照片尚未提供）；Fog 9 tests 與建置通過；App 102 tests 與 iOS 匯出通過。測試使用隔離 SQLite、模擬 Cloud 與合成事件，未代表 Pi 真實部署完成。完整 App 型別檢查仍有先前已知的未使用範本／CSS／路徑問題。
