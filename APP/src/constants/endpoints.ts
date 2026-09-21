/**
 * 後端端點與逾時。**這是 App 端唯一的來源**——原本硬編碼在
 * `HomeScreen.tsx` 的 fetch 裡，換環境要翻程式碼找。
 *
 * ⚠ 對外一律 **3003**。容器內部是 8000，但 compose 發布成 `'3003:8000'`，
 *   所以外面看到的永遠是 3003——防火牆規則、Fog 的 CLOUD_API_URL、這裡，
 *   三處一致。2026-09-13 之前 compose 發布 8000 而其餘是 3003，Pi 打不進來、
 *   每次都降階。契約測試 test_app_endpoints.py 會比對這個檔與 compose。
 *
 * ⚠ IP 是 Tailscale 的。改走 Cloudflare Tunnel 時只要改這個檔。
 */

/** Fog 節點（Node 層）。**主要路徑**——快取、降階都在這一層。 */
export const FOG_URL = 'http://100.86.249.39:3001/query';

/** Cloud 直連。繞過 Fog，用於比對「Fog 有沒有幫上忙」。 */
export const CLOUD_URL = 'http://100.119.217.100:3003/api/analyze';

/**
 * Cloud 直連時送的 API 金鑰，取自 `APP/.env`（已被 .gitignore 擋住）。
 *
 * 2026-09-14 起 Cloud 的 /query、/analyze、/api/analyze 三條都要 X-API-Key，
 * 直連不帶就回 401。Fog 那條路不受影響——金鑰由 Fog 的 cloud_headers() 持有。
 *
 * ⚠ 必須寫成 `process.env.EXPO_PUBLIC_XXX` 這種靜態存取。Expo 是在打包時做
 *   字面替換，解構或用變數取鍵都會取不到值。
 *
 * ⚠ EXPO_PUBLIC_ 的值會被寫進 bundle，拿得到 App 的人就拿得到它。改成這樣
 *   只是不讓金鑰進版控，不等於它變成祕密了。正式路徑仍是 App → Fog → Cloud，
 *   只有 Fog 該持有金鑰（見 server/main.py 的 API_SHARED_SECRET 一節）。
 *
 * 沒設就是空字串，此時不送 X-API-Key——只走 Fog 的人不必設這個變數。
 */
export const CLOUD_API_KEY = process.env.EXPO_PUBLIC_CLOUD_API_KEY ?? '';

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
