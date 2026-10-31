# 儀表板控制雲端中斷模擬

只在比賽 Fog 主動啟用時生效。快取照常查詢、手機與 Pi 的連線保留；不修改 Cloud URL、網卡、路由或防火牆，也不停止 Cloud 服務。已經送出的請求不會被中斷。

## 部署準備

1. 部署本輪新版 Fog（包含 `fog_demo.py`、`main.py`），保留現有 SQLite、快取與佇列檔案。
2. 儀表板主機重啟新版 `tools/pipeline_dashboard.py`，包含新模組 `tools/fog_demo_control.py` 及 `web/pipeline/`。
3. 兩邊設定同一個專用金鑰。儀表板可用環境變數 `FOG_DEMO_CONTROL_TOKEN`，或本機 `.artifacts/fog-demo-control.token`（本次已產生，不納入 Git）。Pi 在既有 Python Fog 服務的環境檔加入下列設定；不可把真實金鑰提交到 Git 或寫入前端。

```dotenv
FOG_DEMO_CONTROL_ENABLED=1
FOG_DEMO_CONTROL_TOKEN=<與儀表板相同的專用金鑰>
FOG_DEMO_NODE_ID=foodscan-pi
PIPELINE_DASHBOARD_URL=<既有可從Pi連到的儀表板基底網址>
```

如需自訂節點 ID，儀表板也設定相同 `FOG_DEMO_NODE_ID`。金鑰僅用於 Fog 讀取命令與回報，不會下發瀏覽器。控制輪詢同樣透過既有 Tailscale／可信網路連線；不要另外對公網開放此 API。

未設定或未啟用時不模擬任何故障。本輪尚未 push／部署 Pi；不能只更新網頁就視為已接上。

## 展示操作

- 先等畫面出現 Fog 控制連線；未收到回報時「模擬雲端斷線」停用。
- 勾選「任務入列後 5 秒自動恢復」，按「模擬雲端斷線」。先顯示等待套用，收到 Fog 確認才顯示已套用。
- 手機正常掃描。快取命中直接回傳，不排隊也不開始倒數；快取未命中才在 Fog 真正寫入佇列，回傳 202 與 queue_id。
- 第一次成功入列後開始倒數；後續重複查詢不重設倒數。倒數到期只解除模擬，原背景 worker 隨後補送，實際 Cloud 不通仍按原機制重試。App 仍須輪詢取得結果，儀表板不偽造手機確認。
- 可隨時按「恢復雲端連線」。取消勾選自動恢復時，需手動恢復；仍有最長 120 秒安全上限。

## 安全恢復與界線

Fog 每秒主動拉取命令與回報。命令有版本，回報與畫面以該版本對應；失去控制心跳約 4 秒內解除模擬，總開啟時間最多 120 秒。服務重啟也從正常狀態開始。解除模擬並不宣稱真實網路已恢復，必須繼續看補送事件。

控制開關作用於這台 Fog 後續的 Cloud 轉送與佇列補送，不只單筆請求；因此限比賽專用 Fog 使用，介面明確標示模擬狀態。正常快取、資料脫敏、佇列持久化及個人化回應不被控制器改寫。

儀表板操作端僅接受本機或既有 localhost Tailscale proxy，並拒絕跨來源請求。Fog 的輪詢與 ACK 需要專用金鑰。這套控制不變更既有 `/api/events` 的信任模型。

## 本輪驗證

相關 27 項測試通過，涵蓋金鑰、來源限制、入列後倒數、手動恢復、心跳逾時、120 秒上限及真實 SQLite 快取／佇列流程。

另在獨立 localhost 測試環境，以真實 Fog HTTP、SQLite 與背景 worker，搭配合成 Cloud 結果驗證：快取命中仍回傳；未命中回傳 202；入列後自動解除；補送完成且可透過 queue 狀態取得結果。沒有連接 Pi、沒有呼叫 Vision／Gemini、沒有手機 ACK。此測試頁為 `http://127.0.0.1:8789/`，不可當作正式 Pi 驗收。

全套 contract 測試本輪為 414 passed、1 failed、1 deselected：失敗是未修改的 `test_fuzzy_correction_corrects_typos_from_tfda_lexicon`（同距離候選由 set 順序選到「芥籽油」，測試期待其他三種油），與此控制流程無關；缺照片的既有 guard 測試維持排除。
