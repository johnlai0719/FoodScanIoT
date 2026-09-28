/**
 * 送出一次分析請求、解讀回應、組出量測紀錄。
 *
 * 為什麼抽出來
 * ----------
 * 一般掃描（HomeScreen）與自動量測（batchRunner）必須走**完全相同**的請求：
 * 同樣的標頭、同樣的逾時、同樣的「什麼算失敗」判準。兩邊各寫一份的話，
 * 量到的就不是使用者實際走的那條路——本專案「同一份知識不要放兩個地方」
 * 的慣例正是為此（族群詞彙曾因此分岔）。
 *
 * 這裡只管網路與解讀，不碰任何畫面狀態。
 */
import { FOG_URL, CLOUD_URL, CLOUD_API_KEY, TESTER_ID, ANALYSIS_TIMEOUT_MS } from '../constants/endpoints';
import * as Telemetry from './telemetry';

export type Endpoint = 'fog' | 'cloud';

export interface QueryOptions {
  endpoint: Endpoint;
  barcode: string;
  images: string[];
  /** X-Bypass-Cache：Fog 跳過「讀」快取（照常寫）。量完整路徑延遲時要開。 */
  bypassCache: boolean;
  /** X-Local-Only：Fog 只用本機 OCR，不轉送雲端。 */
  localOnly: boolean;
  /** 測試時可換掉 fetch；正式使用不傳。 */
  fetchImpl?: typeof fetch;
}

/**
 * 回應的四種結局。**失敗在兩層都是 HTTP 200 ＋ {status, message}**，
 * 不能只看 response.ok——見 CLAUDE.md「分析失敗是 HTTP 200」。
 */
export type QueryKind = 'ok' | 'degraded_local' | 'error';

export interface QueryOutcome {
  kind: QueryKind;
  reqId: string;
  /** 手機自己量的往返時間（同一個時鐘兩次相減）。 */
  totalMs: number;
  httpStatus: number | null;
  headers: Headers | null;
  /** kind 為 ok 或 degraded_local 時才有。 */
  result: any;
  /** kind 為 error 時的訊息（可直接顯示給使用者）。 */
  error: string | null;
}

export async function sendAnalysis(opts: QueryOptions): Promise<QueryOutcome> {
  const doFetch = opts.fetchImpl ?? fetch;
  const reqId = Telemetry.newRequestId();
  const t0 = Date.now();
  let httpStatus: number | null = null;
  let headers: Headers | null = null;
  try {
    const url = opts.endpoint === 'fog' ? FOG_URL : CLOUD_URL;
    // 120 秒比下游每一層都長，好讓後端寫好的錯誤訊息與降階結果送得到手機，
    // 而不是被 App 自己先掐掉。推導見 constants/endpoints.ts。
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ANALYSIS_TIMEOUT_MS);
    let response: Response;
    try {
      response = await doFetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'no-cache',
          'X-Request-Id': reqId,
          // 專用標頭而不是沿用上面那個 Cache-Control——它每次都送，
          // 拿它當判準等於永久關閉快取，就量不出快取有沒有幫上忙。
          ...(opts.bypassCache ? { 'X-Bypass-Cache': '1' } : {}),
          // 本機 OCR 未就緒時 Fog 回 503，**不會**改送雲端。
          ...(opts.localOnly ? { 'X-Local-Only': '1' } : {}),
          ...(opts.endpoint === 'cloud' && CLOUD_API_KEY ? { 'X-API-Key': CLOUD_API_KEY } : {}),
          ...(TESTER_ID ? { 'X-Tester-Id': TESTER_ID } : {}),
        },
        signal: ctrl.signal,
        // 個人化比對完全在本地進行，健康背景不送往後端。
        body: JSON.stringify({ barcode: opts.barcode, label_images: opts.images }),
      });
    } finally {
      clearTimeout(timer);
    }
    httpStatus = response.status;
    headers = response.headers;
    if (!response.ok) throw new Error(`伺服器代碼: ${response.status}`);
    const json = await response.json();
    // Fog 可能將結果包在 data 欄位內
    const result = json.health_score !== undefined ? json : (json.data ?? json);

    // Fog 的本機降階：**有部分結果，不是失敗**。它刻意沒有 health_score
    // （fog/transforms.build_degraded_local_response），不能跟下面的判斷混在一起。
    if (result?.status === 'degraded' && result?.degraded_mode === 'local_ocr') {
      return { kind: 'degraded_local', reqId, totalMs: Date.now() - t0, httpStatus, headers, result, error: null };
    }
    if (result?.health_score === undefined) {
      throw new Error(result?.message ?? json?.message ?? '無法完成分析，請確認條碼或照片後再試一次');
    }
    return { kind: 'ok', reqId, totalMs: Date.now() - t0, httpStatus, headers, result, error: null };
  } catch (err: any) {
    // 「連不上」與「等太久」對使用者是不同的下一步，要分開講。
    const msg = err?.name === 'AbortError'
      ? `分析逾時（超過 ${ANALYSIS_TIMEOUT_MS / 1000} 秒）。請確認網路後重試，或減少照片張數。`
      : err?.message ?? '無法連線至後端分析節點，請確認伺服器有正常運作！';
    return { kind: 'error', reqId, totalMs: Date.now() - t0, httpStatus, headers, result: null, error: msg };
  }
}

/** 批次量測才有的欄位。一般掃描不傳，那幾欄就是空的。 */
export interface RunContext {
  run_id: string;
  run_label: string;
  seq: number;
  /** 逐案跑測試集時才有。 */
  case_id?: string | null;
  pack_version?: string | null;
  compress_ms?: number | null;
}

/**
 * 一次請求 → 一列量測紀錄。**刻意不記照片與成分原文**——一是隱私（紀錄會被
 * 匯出），二是能拿來算指標的是「有幾項」而不是內容。
 */
export function toScanRecord(o: QueryOutcome, opts: QueryOptions, run?: RunContext): Telemetry.ScanRecord {
  const h = (k: string) => o.headers?.get(k) ?? null;
  const r = o.result;
  return {
    request_id: o.reqId,
    at: new Date().toISOString(),
    endpoint: opts.endpoint,
    barcode: opts.barcode,
    n_images: opts.images.length,
    payload_chars: opts.images.reduce((n, b) => n + b.length, 0),
    http_status: o.httpStatus,
    total_ms: o.totalMs,
    fog_node: Telemetry.parseTiming(h('X-Timing-Fog-Node')),
    fog_py: Telemetry.parseTiming(h('X-Timing-Fog-Py')),
    cloud: Telemetry.parseTiming(h('X-Timing-Cloud')),
    cache: h('X-Cache'),
    bypass_cache: opts.bypassCache,
    local_only: opts.localOnly,
    status: r?.status ?? null,
    health_score: r?.health_score ?? null,
    risk_level: r?.risk_level ?? null,
    n_additives: r?.ingredients_detail?.length ?? null,
    // 降階與失敗要分開統計。degraded_mode 是 Fog 本機 OCR 那條路，與 Cloud 的診斷降級不同。
    degraded: r?.status === 'degraded' || !!r?.degraded_mode,
    // Cloud 連不上時 Fog 回的**過期快取**。Python 層會標 X-Cache: STALE，但 Node 層
    // 只轉發自己的 X-Cache（會寫 MISS），所以只能從回應本文的 _offline_mode 認出來。
    offline_mode: !!r?._offline_mode,
    error: o.error,
    run_id: run?.run_id ?? null,
    run_label: run?.run_label ?? null,
    seq: run?.seq ?? null,
    case_id: run?.case_id ?? null,
    pack_version: run?.pack_version ?? null,
    compress_ms: run?.compress_ms ?? null,
  };
}
