import fetch from 'node-fetch';
import { AnalysisResultLike, FogQueryRequest } from './types';
import { getCache, setCache, getStaleCache } from './cache';
import { emitFog, resultSummary } from './pipelineEvents';
import { claimNextMiss } from './rehearsalControl';

// 延遲埋點。**只量本行程內的時距，不傳時間戳**——決策單 #15 未定的正是
// 「跨機器時鐘怎麼校正」，而手機／Pi／雲端三個時鐘任兩個相減都含未知偏移。
// 格式與 Python 兩層共用（server/telemetry.py 的 format_timing）：
//   key=毫秒，以 ; 分隔，ASCII only（標頭不可放中文）
function fmtTiming(seg: Record<string, number | null | undefined>): string {
  return Object.entries(seg)
    // null 略過而不寫成 0：「沒有這一段」與「這一段 0 毫秒」是兩件事
    .filter(([, v]) => v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${Math.round(v as number)}`)
    .join(';');
}

// 使用 127.0.0.1 代替 localhost 以提高穩定性
const CLOUD_API_URL = process.env.PYTHON_API_URL || 'http://127.0.0.1:3002/query';
// 2026-09-13：60 → 105 秒。這一層要比 Python 層的 CLOUD_READ_TIMEOUT（90 秒）
// 長，否則 Node 會先放棄，Python 層的降階邏輯根本沒機會跑到。
// 改用 vlcrop 後 Cloud 端單張約 10.3 秒（實測穩態；同日稍早量到的 55 秒是
// VRAM 擠爆造成的，已用參數解掉），三張約 31 秒——60 秒沒有餘裕。
const CLOUD_TIMEOUT = 105000;

// 與 server/telemetry.py 的 _SAFE_ID 相同（tests/contract/test_timing_headers.py 比對兩者）。
// request id 來自 App、會被原樣轉發，不合格就不轉，避免把使用者輸入塞進標頭。
const SAFE_REQUEST_ID = /^[A-Za-z0-9_.:-]{1,64}$/;

export async function handleQuery(reqData: FogQueryRequest, requestId?: string, bypassCache = false, localOnly = false, demoScenario?: string): Promise<{ status: number, data: any, cacheHeader: string, headers: Record<string, string> }> {
  const { barcode } = reqData;
  const startTime = Date.now();
  // App 產生的 request id 要逐層往下帶，三層的計時紀錄才併得起來。
  const rid = requestId && SAFE_REQUEST_ID.test(requestId) ? requestId : '';
  const scenario = ['cloud', 'miss', 'cache', 'queue'].includes(demoScenario || '') ? demoScenario : undefined;
  const observe = (event: Parameters<typeof emitFog>[1], status: 'active' | 'done' | 'failed' = 'active',
    detail: Record<string, unknown> = {}, payload: Record<string, unknown> = {}) =>
    emitFog(rid, event, status, detail, payload);
  observe('received', 'active', {}, { image_count: reqData.label_images?.length || 0,
    ...(scenario ? { demo_scenario: scenario } : {}) });
  const upstreamHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(rid ? { 'X-Request-Id': rid } : {}),
    // Preserve legacy upload opt-out; Python rejects it without uploading or queueing.
    ...(localOnly ? { 'X-Local-Only': '1' } : {}),
    ...(bypassCache ? { 'X-Bypass-Cache': '1' } : {}),
    ...(scenario ? { 'X-Demo-Scenario': scenario } : {}),
  };
  // 下游（Python → Cloud）自己量的那幾段，原樣往上帶，讓 App 一次收齊三層。
  const passThrough: Record<string, string> = {};
  let upstreamMs: number | null = null;
  const collect = (r: { headers: { get(n: string): string | null } }) => {
    for (const h of ['X-Timing-Cloud', 'X-Timing-Fog-Py', 'X-Request-Id']) {
      const v = r.headers.get(h);
      if (v) passThrough[h] = v;
    }
  };
  const timingHeaders = () => ({
    // 自己先帶上：Python 連不上時 passThrough 是空的，最需要對帳的正是那幾次
    ...(rid ? { 'X-Request-Id': rid } : {}),
    ...passThrough,
    'X-Timing-Fog-Node': fmtTiming({ upstream: upstreamMs, total: Date.now() - startTime }),
  });

  // 1. 查詢快取 (命中後仍需送往 Python 做格式正規化；個人化已移至 App 端)
  //
  // bypassCache：測試模式**只跳過讀，照常寫**。
  // 量延遲時每一次都要走完整路徑（App → Fog → Cloud → reader），
  // 命中快取的那幾次會把中位數拉到失真。照常寫則是為了讓快取本身
  // 仍可被量測——關掉寫入的話就量不出「Fog 有沒有幫上忙」。
  //
  // ⚠ 刻意**不用** App 每次都送的 `Cache-Control: no-cache` 當判準：
  //   那個標頭每一次請求都在，拿它當開關等於永久關閉快取。
  // 只用本機辨識時也不可讀快取：快取裡是雲端算過的完整結果，拿它回應等於
  // 沒有照使用者的選擇做，而畫面會顯示成完整分析，使用者無從察覺。
  const isUsable = (result: any) => result?.status === 'success'
    && result?.overall_summary !== '診斷引擎暫時降級運作。';
  if (!bypassCache && !localOnly) await claimNextMiss(barcode, rid);
  const freshCandidate = (bypassCache || localOnly || barcode === 'TEST') ? null : getCache(barcode);
  const fresh = isUsable(freshCandidate) ? freshCandidate : null;
  const staleCandidate = (bypassCache || localOnly || barcode === 'TEST') ? null : getStaleCache(barcode);
  const cachedData = fresh || (isUsable(staleCandidate) ? staleCandidate : null);
  const cacheWarning = cachedData && !fresh ? { _warning: '此為本地舊快取，尚未取得最新資料' } : {};
  if (cachedData) {
    observe('cache_hit', 'active', { cache_status: fresh ? 'HIT' : 'HIT-STALE' });
    console.log(`[HIT] ${barcode} -> Normalizing cached result...`);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000); // 正規化給 15 秒就好

    try {
      const _t0 = Date.now();
      const response = await fetch(CLOUD_API_URL, {
        method: 'POST',
        headers: upstreamHeaders,
        body: JSON.stringify({
          ...reqData,
          cached_result: cachedData 
        }),
        signal: controller.signal
      });

      clearTimeout(timeoutId);
      upstreamMs = Date.now() - _t0;
      collect(response);

      if (response.ok) {
        const normalizedResult = await response.json() as AnalysisResultLike;
        observe('cache_returned', 'done', { cache_status: fresh ? 'HIT' : 'HIT-STALE', http_status: 200 },
          { ...resultSummary(normalizedResult), cached: true });
        return {
          status: 200,
          data: {
            ...normalizedResult,
            cached: true,
            ...cacheWarning
          },
          cacheHeader: fresh ? 'HIT' : 'HIT-STALE',
          headers: timingHeaders()
        };
      } else {
        console.warn(`[WARN] Python re-calc failed: ${response.status}`);
        observe('cache_returned', 'done', { cache_status: 'HIT-RAW', http_status: 200 },
          { ...resultSummary(cachedData), cached: true });
        return { status: 200, data: { ...cachedData, cached: true, ...cacheWarning },
          cacheHeader: 'HIT-RAW', headers: timingHeaders() };
      }
    } catch (e: any) {
      clearTimeout(timeoutId);
      console.warn(`[WARN] Python re-calc error: ${e.message}`);
      observe('cache_returned', 'done', { cache_status: 'HIT-RAW', http_status: 200 },
        { ...resultSummary(cachedData), cached: true });
      return {
        status: 200,
        data: {
          ...cachedData,
          cached: true,
          ...cacheWarning
        },
        cacheHeader: 'HIT-RAW',
        headers: timingHeaders()
      };
    }
  }

  // 2. 快取未命中，轉發至 Cloud
  if (!localOnly) observe(bypassCache ? 'cache_bypass' : barcode === 'TEST' ? 'cache_excluded' : 'cache_miss',
    'active', { cache_status: bypassCache ? 'BYPASS' : barcode === 'TEST' ? 'EXCLUDED' : 'MISS' });
  observe('manager_forwarded');
  console.log(`[MISS] ${barcode} -> Forwarding to Python/Cloud...`);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CLOUD_TIMEOUT);

  try {
    const _t0 = Date.now();
    const response = await fetch(CLOUD_API_URL, {
      method: 'POST',
      headers: upstreamHeaders,
      body: JSON.stringify(reqData),
      signal: controller.signal
    });

    clearTimeout(timeoutId);
    upstreamMs = Date.now() - _t0;
    collect(response);

    if (response.ok) {
      const cloudResult = await response.json() as AnalysisResultLike;
      if (cloudResult.status === 'queued') {
        observe('queued', 'active', { cache_status: 'QUEUED', queue_id: cloudResult.queue_id, http_status: 202 },
          cloudResult.request_id && cloudResult.request_id !== rid ? { canonical_request_id: cloudResult.request_id } : {});
      } else {
        observe('manager_returned', 'done', { http_status: response.status }, resultSummary(cloudResult));
      }
      
      // 🛠️ 修正：只有當結果狀態為成功時，才寫入快取
      const isDegraded = cloudResult.overall_summary === '診斷引擎暫時降級運作。';
      if (cloudResult.status === 'success' && !isDegraded && barcode !== 'TEST') {
        console.log(`[OK] ${barcode} cached`);
        setCache(barcode, cloudResult);
      } else {
        console.log(`[INFO] ${barcode} skipped cache (status: ${cloudResult.status}, degraded: ${isDegraded})`);
      }
      
      return {
        status: response.status,
        data: {
          ...cloudResult,
          cached: false
        },
        cacheHeader: cloudResult.status === 'queued' ? 'QUEUED' : (bypassCache ? 'BYPASS' : 'MISS'),
        headers: timingHeaders()
      };
    } else {
      observe('unavailable', 'failed', { http_status: response.status });
      return { status: response.status, data: await response.json(),
        cacheHeader: 'ERROR', headers: timingHeaders() };
    }

  } catch (error: any) {
    observe('unavailable', 'failed', { http_status: 503 });
    clearTimeout(timeoutId);
    console.error(`[ERR] ${barcode} -> Cloud Timeout/Error: ${error.message}`);

    // Python is unavailable: no durable queue acknowledgement was received.
    return {
      status: 503,
      data: { status: 'error', queued: false,
        message: '目前無法連線 Fog 管理服務，資料尚未暫存。請保留照片並稍後重試。' },
      cacheHeader: 'ERROR', headers: timingHeaders()
    };
  }
}
