# web — 添加物開放查詢平台與管理後台

React＋Vite。直接讀 Cloud 的 API。

| 路徑 | 畫面 |
|---|---|
| `/` | 添加物搜尋（`SearchPortal`） |
| `/additive/:id` | 添加物詳情：用途、使用範圍與限量、附來源的族群注意事項；可提交勘誤建議（`AdditiveDetail`） |
| `/admin/login`、`/admin/dashboard` | 管理後台：審核勘誤建議、管理食安事件（`AdminLogin`、`AdminDashboard`） |

## 開發

```bash
npm install
npm run dev
```

後端位址在 `src/config.js`，預設指向 Tailscale 上的 Cloud（:3003）。本機開發可建立 `.env.local`：

```
VITE_API_BASE=http://127.0.0.1:3003
```
