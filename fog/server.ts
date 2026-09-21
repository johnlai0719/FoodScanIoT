import express, { Request, Response, NextFunction } from 'express';
import cors from 'cors';
import morgan from 'morgan';
import dotenv from 'dotenv';
import { FogQueryRequest } from './types';
import { initDB } from './cache';
import { handleQuery } from './queryHandler';

dotenv.config();

/**
 * 目前執行中的程式版本。
 *
 * 取得順序與 fog/version.py 一致：APP_COMMIT 環境變數 → git rev-parse → "unknown"。
 * 部署腳本以 `git reset --hard origin/main` 更新，因此 Pi 上的 HEAD 必然等於本次
 * 部署的 commit，讀 git 即可——毋須透過 `sudo systemctl set-environment` 注入環境
 * 變數（那會是另一道 sudo 指令，若未列入 NOPASSWD 會讓非互動式部署卡在密碼提示）。
 *
 * 於啟動時計算一次並快取；健康檢查會被頻繁呼叫，不應每次都開子行程。
 */
const APP_COMMIT: string = (() => {
  if (process.env.APP_COMMIT) return process.env.APP_COMMIT.trim();
  try {
    return require('child_process')
      .execSync('git rev-parse --short HEAD', { cwd: __dirname, timeout: 3000 })
      .toString()
      .trim() || 'unknown';
  } catch {
    return 'unknown';
  }
})();

// 初始化快取資料庫
initDB();

const app = express();
const PORT = process.env.NODE_PORT || 3001;

// Middlewares
app.use(cors());
app.use(express.json({ limit: '10mb' }));
app.use(morgan(':method :url :status :res[content-length] - :response-time ms - barcode: :barcode'));

// 自定義 Morgan Token 記錄 Barcode
morgan.token('barcode', (req: Request) => {
  return (req.body as FogQueryRequest)?.barcode || 'N/A';
});

/**
 * 請求驗證 Middleware
 */
const validateQuery = (req: Request, res: Response, next: NextFunction) => {
  const { barcode } = req.body as FogQueryRequest;

  // 驗證條碼：允許 8-13 位數字，或者特殊字串 "TEST"
  if (!barcode || (!/^\d{8,13}$/.test(barcode) && barcode !== "TEST")) {
    return res.status(400).json({
      error: "無效的條碼格式，須為 8-13 位數字或測試字串",
      code: 400
    });
  }

  // 註：原本此處要求 user_conditions.group 必填。個人化已於 2026-08-04 移至 App 端，
  //     健康背景不再離開裝置，故移除該項驗證。
  next();
};

/**
 * 健康檢查。部署腳本（.github/workflows/deploy-fog.yml）會 curl 此端點確認
 * 服務重啟後有沒有活過來——先前這個端點並不存在，導致部署最後一步必定失敗。
 *
 * 設計原則：下游狀態只作為資訊回報，不影響自身的 status。Node 層在 Python 層
 * 失敗時仍能以 HIT-RAW 回應快取，把下游算進自身健康度會讓部署在不必要的時候失敗。
 * 預設不做網路呼叫；`?deep=1` 才會實際連線 Python 層。
 */
app.get('/health', async (req: Request, res: Response) => {
  const payload: Record<string, unknown> = {
    status: 'ok',
    layer: 'fog-node',
    commit: APP_COMMIT,
  };

  if (req.query.deep) {
    const PYTHON_URL = process.env.PYTHON_API_URL?.replace('/query', '') || 'http://127.0.0.1:3002';
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);
    try {
      const r = await fetch(`${PYTHON_URL}/health`, { signal: controller.signal });
      payload.downstream = { 'fog-python': r.ok ? 'ok' : `http_${r.status}` };
    } catch (err: any) {
      payload.downstream = { 'fog-python': `unreachable: ${err?.name ?? 'Error'}` };
    } finally {
      clearTimeout(timeoutId);
    }
  }

  res.json(payload);
});

// 路由: POST /query
app.post('/query', validateQuery, async (req: Request, res: Response, next: NextFunction) => {
  try {
    // X-Bypass-Cache：量測用的測試模式。**專用標頭而不是 Cache-Control**——
    // App 每一次請求都送 `Cache-Control: no-cache`，拿它當判準等於永久關閉快取，
    // 那就量不出「Fog 的快取有沒有幫上忙」。
    const bypass = req.header('X-Bypass-Cache') === '1';
    // X-Local-Only：使用者主動要求只用本機辨識，不轉發雲端。
    // 這不是故障時的降階，是**使用者選的**——照片因此不離開這台邊緣節點。
    // 兩者走同一條產出路徑（本機 OCR），但觸發來源不同，畫面上要講清楚是哪一種。
    const localOnly = req.header('X-Local-Only') === '1';
    // X-Tester-Id：測試者代號，原樣往下帶到 Cloud。Fog 自己不用它做任何判斷——
    // 快取、降階、脫敏都與是誰傳的無關，這裡只負責不要把它弄丟。
    const result = await handleQuery(
      req.body as FogQueryRequest, req.header('X-Request-Id'), bypass, localOnly,
      req.header('X-Tester-Id'));
    res.header('X-Cache', result.cacheHeader);
    // 三層的計時標頭。**不放進 body**：`test_cloud_response_contract.py` 是
    // `set(keys) == EXPECTED` 嚴格相等，動 body 等於改 App 的資料契約；
    // 而標頭在降階與錯誤回應上同樣帶得到——那正是最需要量的那幾次。
    for (const [k, v] of Object.entries(result.headers ?? {})) res.header(k, v);
    res.status(result.status).json(result.data);
  } catch (err) {
    next(err);
  }
});

// 路由: DELETE /cache (轉發給 Python 處理)
app.delete('/cache', async (req: Request, res: Response) => {
  try {
    const PYTHON_URL = process.env.PYTHON_API_URL?.replace('/query', '') || 'http://127.0.0.1:3002';
    const response = await fetch(`${PYTHON_URL}/cache`, { method: 'DELETE' });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    res.status(500).json({ error: "無法連線至 Python 端進行清除" });
  }
});

app.delete('/cache/:barcode', async (req: Request, res: Response) => {
  try {
    const { barcode } = req.params;
    const PYTHON_URL = process.env.PYTHON_API_URL?.replace('/query', '') || 'http://127.0.0.1:3002';
    const response = await fetch(`${PYTHON_URL}/cache/${barcode}`, { method: 'DELETE' });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    res.status(500).json({ error: "無法連線至 Python 端進行清除" });
  }
});

// 統一錯誤處理
app.use((err: any, req: Request, res: Response, next: NextFunction) => {
  console.error(`[Error] ${err.stack}`);
  res.status(err.status || 500).json({
    error: err.message || "伺服器內部錯誤",
    code: err.status || 500
  });
});

app.listen(Number(PORT), "0.0.0.0", () => {
  console.log(`🚀 Fog Server (Edge Node) is running on http://0.0.0.0:${PORT}`);
  // 提示使用者檢查真實 IP，避免抓到虛擬網卡
  console.log(`📡 Access from mobile using your Raspberry Pi's REAL IP on port ${PORT}`);
  console.log(`💡 Example: http://192.168.30.94:${PORT}/query`);
});
