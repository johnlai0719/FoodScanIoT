import fetch from 'node-fetch';
import { AnalysisResultLike, FogQueryRequest } from './types';
import { getCache, setCache, getStaleCache } from './cache';

// 使用 127.0.0.1 代替 localhost 以提高穩定性
const CLOUD_API_URL = process.env.PYTHON_API_URL || 'http://127.0.0.1:3002/query';
const CLOUD_TIMEOUT = 60000; // 提高到 60 秒超時，對應視覺分析

export async function handleQuery(reqData: FogQueryRequest): Promise<{ status: number, data: any, cacheHeader: string }> {
  const { barcode } = reqData;
  const startTime = Date.now();

  // 1. 查詢快取 (命中後仍需送往 Python 做格式正規化；個人化已移至 App 端)
  const cachedData = getCache(barcode);
  if (cachedData) {
    console.log(`[HIT] ${barcode} -> Normalizing cached result...`);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000); // 正規化給 15 秒就好

    try {
      const response = await fetch(CLOUD_API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...reqData,
          cached_result: cachedData 
        }),
        signal: controller.signal
      });

      clearTimeout(timeoutId);

      if (response.ok) {
        const normalizedResult = await response.json() as AnalysisResultLike;
        return {
          status: 200,
          data: {
            ...normalizedResult,
            cached: true
          },
          cacheHeader: 'HIT'
        };
      } else {
        console.warn(`[WARN] Python re-calc failed: ${response.status}`);
      }
    } catch (e: any) {
      clearTimeout(timeoutId);
      console.warn(`[WARN] Python re-calc error: ${e.message}`);
      return {
        status: 200,
        data: {
          ...cachedData,
          cached: true
        },
        cacheHeader: 'HIT-RAW'
      };
    }
  }

  // 2. 快取未命中，轉發至 Cloud
  console.log(`[MISS] ${barcode} -> Forwarding to Python/Cloud...`);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CLOUD_TIMEOUT);

  try {
    const response = await fetch(CLOUD_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqData),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    if (response.ok) {
      const cloudResult = await response.json() as AnalysisResultLike;
      
      // 🛠️ 修正：只有當結果狀態為成功時，才寫入快取
      const isDegraded = cloudResult.overall_summary === '診斷引擎暫時降級運作。';
      if (cloudResult.status === 'success' && !isDegraded) {
        console.log(`[OK] ${barcode} cached`);
        setCache(barcode, cloudResult);
      } else {
        console.log(`[INFO] ${barcode} skipped cache (status: ${cloudResult.status}, degraded: ${isDegraded})`);
      }
      
      return {
        status: 200,
        data: {
          ...cloudResult,
          cached: false
        },
        cacheHeader: 'MISS'
      };
    } else {
      throw new Error(`Cloud Status: ${response.status}`);
    }

  } catch (error: any) {
    clearTimeout(timeoutId);
    console.error(`[ERR] ${barcode} -> Cloud Timeout/Error: ${error.message}`);

    // 3. 降階回應 (Fallback)
    // 嘗試取得過期快取
    const staleData = getStaleCache(barcode);
    if (staleData) {
      console.log(`[DEGRADED] ${barcode} (Using stale cache)`);
      return {
        status: 200,
        data: {
          ...staleData,
          cached: true,
          _warning: "⚠️ 目前無法取得最新資料，為您顯示舊有快取"
        },
        cacheHeader: 'DEGRADED'
      };
    }

    // 完全無快取，回傳降階錯誤訊息
    return {
      status: 200,
      data: {
        status: "degraded",
        message: "目前無法取得最新資料且無快取，請稍後再試",
        cached: false,
        cached_at: null
      },
      cacheHeader: 'DEGRADED'
    };
  }
}
