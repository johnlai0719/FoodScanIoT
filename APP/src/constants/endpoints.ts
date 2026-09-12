/**
 * 後端端點與逾時。**這是 App 端唯一的來源**——原本硬編碼在
 * `HomeScreen.tsx` 的 fetch 裡，換環境要翻程式碼找。
 *
 * ⚠ 埠號有兩套，很容易搞錯（CLAUDE.md 也記過）：
 *   `server/main.py` 單獨執行時預設 `CLOUD_PORT=3003`，
 *   但 `docker-compose.yml` 與 `server/.env` 用的是 **8000**，
 *   而正式管線就是用 compose 起的。2026-09-13 之前這裡寫 3003，打不到。
 *
 * ⚠ IP 是 Tailscale 的。改走 Cloudflare Tunnel 時只要改這個檔。
 */

/** Fog 節點（Node 層）。**主要路徑**——快取、降階都在這一層。 */
export const FOG_URL = 'http://100.86.249.39:3001/query';

/** Cloud 直連。繞過 Fog，用於比對「Fog 有沒有幫上忙」。 */
export const CLOUD_URL = 'http://100.119.217.100:8000/api/analyze';

/**
 * 分析請求的逾時（毫秒）。
 *
 * 這是**安全網不是預期等待時間**。2026-09-13 改用 vlcrop 之後實測單張
 * 10.3 秒、三張約 31 秒（Gemini 時代是 13～16 秒／次）。
 *
 * 120 秒的取法是「比下游每一層都長」，否則 App 會先放棄，後端寫好的
 * 錯誤訊息與降階結果就永遠送不到使用者眼前：
 *
 *     App            120s  ← 這裡
 *     Fog Node       105s  fog/queryHandler.ts   CLOUD_TIMEOUT
 *     Fog Python      90s  fog/main.py           CLOUD_READ_TIMEOUT
 *     Cloud → reader 120s  server/vision_backend READER_TIMEOUT
 *
 * 在這之前 App 完全沒有設逾時，靠平台預設（各家不同，可能數分鐘或無限），
 * 網路斷掉時畫面會一直轉。
 */
export const ANALYSIS_TIMEOUT_MS = 120_000;
