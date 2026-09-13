/**
 * 實驗量測的紀錄與匯出。一次掃描存一列，之後整批匯出做統計。
 *
 * 為什麼存在 App 而不是伺服器
 * ------------------------
 * 要量的其中一段就是「手機到 Fog」，那一段**只有手機量得到**。而個人化資料
 * 不上雲是本專案明文的界線（CLAUDE.md「慣例」），所以紀錄也留在裝置上，
 * 由使用者自己匯出——不自動回傳。
 *
 * 時鐘的處理
 * --------
 * 決策單 #15「五段延遲的時鐘校正方式」至今未定，理由是**跨機器相減會被時鐘
 * 偏移汙染**。所以這裡也不比對不同機器的時刻：
 *   - `total_ms` 是手機自己量的（`Date.now()` 兩次相減，同一個時鐘）
 *   - 其餘每一段都是那一層自己量的時距，經 HTTP 標頭帶回來
 *   - 「網路時間」不直接量，用相鄰兩層的差推估——那個推估仍無需校正
 *
 * ⚠ `Date.now()` 會被系統校時影響。RN 沒有穩定可用的單調時鐘，
 *   所以極少數紀錄可能出現負值或暴增；統計時用中位數而不是平均，
 *   並保留原始列以便剔除。
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'foodscan.telemetry.v1';
/** 上限。超過就丟最舊的——實驗跑久了不該把裝置存滿。 */
const MAX_RECORDS = 500;

export type TimingSegments = Record<string, number>;

export interface ScanRecord {
  /** 與三層的紀錄對得起來的關聯鍵，由 App 產生並逐層轉發。 */
  request_id: string;
  /** ISO 時刻。**只用於排序與人工對照，不參與任何相減。** */
  at: string;
  endpoint: 'fog' | 'cloud';
  barcode: string;
  n_images: number;
  /** 送出去的 base64 總長度，用來看「圖片大小 vs 延遲」有沒有關係。 */
  payload_chars: number;

  http_status: number | null;
  /** 手機自己量的往返時間（同一個時鐘，可信）。 */
  total_ms: number;
  /** 各層自己量的時距，key 見 server/telemetry.py 的說明。 */
  fog_node: TimingSegments | null;
  fog_py: TimingSegments | null;
  cloud: TimingSegments | null;
  /** `X-Cache`：HIT／HIT-RAW／MISS／DEGRADED。 */
  cache: string | null;

  // ── 結果面。**刻意記「有幾項」而不是記內容** ────────────────────────────
  // 一是隱私（照片與成分原文不留在紀錄裡），二是這些才是能拿來算指標的欄位。
  status: string | null;
  health_score: number | null;
  risk_level: string | null;
  n_additives: number | null;
  /** 降階與失敗要能分開統計——那是最需要量的兩類。 */
  degraded: boolean;
  error: string | null;
}

export function newRequestId(): string {
  // 只用標頭安全的字元（server/telemetry.py 的 _SAFE_ID 會擋掉其餘的）。
  const r = Math.random().toString(36).slice(2, 10);
  return `app-${Date.now().toString(36)}-${r}`;
}

/** `vision=10412;total=13150` → `{vision:10412,total:13150}`。壞的段落丟掉。 */
export function parseTiming(value: string | null): TimingSegments | null {
  if (!value) return null;
  const out: TimingSegments = {};
  for (const part of value.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    const n = Number(part.slice(i + 1).trim());
    if (Number.isFinite(n)) out[part.slice(0, i).trim()] = n;
  }
  return Object.keys(out).length ? out : null;
}

export async function load(): Promise<ScanRecord[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as ScanRecord[]) : [];
  } catch {
    // 讀不到就當成空的。埋點壞掉不該讓 App 起不來。
    return [];
  }
}

export async function append(rec: ScanRecord): Promise<void> {
  try {
    const all = await load();
    all.push(rec);
    await AsyncStorage.setItem(
      KEY, JSON.stringify(all.slice(-MAX_RECORDS)));
  } catch {
    // 寫不進去就算了——量測不該影響使用者實際在做的事。
  }
}

export async function clear(): Promise<void> {
  try {
    await AsyncStorage.removeItem(KEY);
  } catch { /* 同上 */ }
}

/**
 * 匯出成 CSV。欄位是攤平的，可直接丟進 pandas／Excel。
 *
 * 各層的段落名稱是動態的（reader 的段落會隨管線增減），所以先掃過全部紀錄
 * 收集欄位，缺的填空字串——**不填 0**，「沒量到」與「0 毫秒」是兩件事。
 */
export function toCSV(records: ScanRecord[]): string {
  const segCols = new Set<string>();
  for (const r of records) {
    for (const [layer, seg] of [['node', r.fog_node], ['py', r.fog_py], ['cloud', r.cloud]] as const) {
      if (seg) for (const k of Object.keys(seg)) segCols.add(`${layer}_${k}`);
    }
  }
  const base = ['request_id', 'at', 'endpoint', 'barcode', 'n_images', 'payload_chars',
                'http_status', 'total_ms', 'cache', 'status', 'health_score',
                'risk_level', 'n_additives', 'degraded', 'error'];
  const cols = [...base, ...[...segCols].sort()];

  const cell = (v: unknown) => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [cols.join(',')];
  for (const r of records) {
    const flat: Record<string, unknown> = { ...r };
    for (const [layer, seg] of [['node', r.fog_node], ['py', r.fog_py], ['cloud', r.cloud]] as const) {
      if (seg) for (const [k, v] of Object.entries(seg)) flat[`${layer}_${k}`] = v;
    }
    lines.push(cols.map(c => cell(flat[c])).join(','));
  }
  return lines.join('\n');
}

/** 現場快速看一眼用的摘要。中位數而不是平均——見檔頭關於時鐘的但書。 */
export function summarize(records: ScanRecord[]) {
  const ok = records.filter(r => r.http_status === 200 && !r.degraded && !r.error);
  const med = (xs: number[]) => {
    if (!xs.length) return null;
    const s = [...xs].sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  };
  return {
    n: records.length,
    n_ok: ok.length,
    n_degraded: records.filter(r => r.degraded).length,
    n_error: records.filter(r => r.error).length,
    median_total_ms: med(ok.map(r => r.total_ms)),
    median_cloud_vision_ms: med(ok.map(r => r.cloud?.vision).filter((v): v is number => v != null)),
    median_additives: med(ok.map(r => r.n_additives).filter((v): v is number => v != null)),
  };
}
