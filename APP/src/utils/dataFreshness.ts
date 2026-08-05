/**
 * 資料新鮮度的呈現
 *
 * 2026-08-05 新增。在此之前結果頁顯示的是寫死的「資料校準時間：2026」，
 * 對使用者沒有任何資訊價值——他看不出手上這份分析是剛算出來的，還是 Fog 快取裡
 * 一小時前的舊資料，更看不出 Cloud 斷線時拿到的是過期資料。
 *
 * 資料來源是 Cloud 回應的 `processed_at`（`build_response()` 產生，Fog 不會改動），
 * 代表**這份分析實際被計算出來的時間**——快取命中時它仍是原始計算時間，正是我們
 * 想告訴使用者的「上次更新」。
 */

/** Fog 在 Cloud 不可用、改以過期快取回應時會帶上的旗標 */
const STALE_WARNING_KEY = '_warning';

export interface FreshnessInput {
  /** Cloud 計算此分析的時間（ISO 8601） */
  processed_at?: string;
  /** Fog 是否由快取回應 */
  cached?: boolean;
  /** Fog 降級提示（Cloud 不可用時才有） */
  [STALE_WARNING_KEY]?: string;
}

export interface Freshness {
  /** 主要文字，例如「更新於 3 分鐘前」 */
  label: string;
  /** 是否應以警示樣式呈現（資料明顯過舊或為降級回應） */
  isStale: boolean;
  /** 降級說明，僅在 Cloud 不可用時有值 */
  warning?: string;
}

/** 超過這個秒數就視為「明顯過舊」，值得提醒使用者。24 小時。 */
const STALE_THRESHOLD_SECONDS = 24 * 60 * 60;

/**
 * 把 ISO 時間字串轉為相對時間描述。
 * 無法解析、或時間在未來（裝置時鐘不準）時回傳 null，由呼叫端決定如何降級呈現。
 */
export function formatRelativeTime(iso: string | undefined, now: Date = new Date()): string | null {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;

  const diffSec = Math.floor((now.getTime() - then.getTime()) / 1000);
  // 允許 60 秒內的時鐘誤差；再往前就不猜了，改顯示絕對時間
  if (diffSec < -60) return null;
  if (diffSec < 60) return '剛剛';

  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} 分鐘前`;

  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小時前`;

  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 30) return `${diffDay} 天前`;

  return null; // 太久遠，交給絕對時間顯示
}

/** 絕對時間，作為相對時間無法表達時的後備 */
export function formatAbsoluteTime(iso: string | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * 產生要顯示在結果頁的資料新鮮度資訊。
 *
 * 刻意不在無法判斷時編造內容——沒有 `processed_at` 就說「時間不明」，
 * 而不是顯示當下時間假裝資料是新的。
 */
export function getDataFreshness(
  result: FreshnessInput | null | undefined,
  now: Date = new Date(),
): Freshness {
  const warning = result?.[STALE_WARNING_KEY];
  const iso = result?.processed_at;

  const relative = formatRelativeTime(iso, now);
  const absolute = formatAbsoluteTime(iso);

  if (!relative && !absolute) {
    return { label: '更新時間不明', isStale: true, warning };
  }

  const label = `更新於 ${relative ?? absolute}`;

  let isStale = Boolean(warning);
  if (!isStale && iso) {
    const ageSec = (now.getTime() - new Date(iso).getTime()) / 1000;
    isStale = ageSec > STALE_THRESHOLD_SECONDS;
  }

  return { label, isStale, warning };
}
