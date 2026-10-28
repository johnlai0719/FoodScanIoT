# InnoServe 決賽版

更新：2026-10-28。開發分支：`codex/innoserve-gemini-finals`；本機 worktree：`D:/FoodScanIot/比賽/專案`。同一 repository 的獨立分支，未合併回其他開發分支。

## 當前方向

聚焦「掃描到、讀取與解釋」：拍照 → Fog → Cloud Vision → 文字與位置規則分區 → Gemini 結構化理解 → Fog／App。新 Vision reader 已單張跑通，完整手機端到端仍待驗證。

展示預備分三種情境：首次雲端分析、重複掃描命中Fog快取、Fog對外斷線時的已驗證本地能力。三種情境尚未全部實機跑通；本頁不宣稱已完成離線分析。

| 項目 | 狀態 |
|---|---|
| Vision與兩類區域規則 | 原型已實作，有限樣本，尚非正式版型驗收 |
| Live Pipeline儀表板 | 可觀察階段與點選查看輸出；App顯示回報已有接點 |
| 新Vision reader | `tools/reader_vision_competition.py`，選擇性啟動8181，非既有EasyOCR reader的自動替換 |
| EasyOCR | 保留既有競賽基底；Pi實際速度與離線辨識品質待驗證。既有8180 reader僅做偵測裁切再交Gemini，不能當作已具備完整離線辨識 |
| Search／廠商事件 | 封存為獨立實驗，預設收合，不接掃描、評分或正式事件庫 |
| Tesseract | 本機測試工具，非替代方案；Pi尚未量測，輸出數字有誤辨 |
| 追問助手／雙語方案 | 歷史準備方案，尚未完成，不是本輪主展示需求 |

## 文件入口

- [啟動與隔離](開發環境.md)
- [展示材料與本機實驗索引](材料索引.md)
- [驗證範圍與缺口](驗證紀錄.md)
- [儀表板操作與設定](../web/pipeline/README.md)

這個分支提交程式、設定範本與小型必要資料；憑證、`.env`、模型、照片、資料庫與大型實驗輸出留在本機。不自動部署，不切換既有研究服務。
