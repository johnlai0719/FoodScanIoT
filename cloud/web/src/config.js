// 後端 API 位址集中設定
// 預設指向 Tailscale 上的 Cloud server（100.126.156.60:3003）
// 本機開發時可建立 .env.local 設定 VITE_API_BASE=http://127.0.0.1:3003 覆蓋
export const API_BASE = import.meta.env.VITE_API_BASE || 'http://100.126.156.60:3003';
