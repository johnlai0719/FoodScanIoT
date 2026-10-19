/**
 * 自動量測：同一組條碼／照片連續送出 N 次，只累積量測紀錄，不顯示結果。
 *
 * 用途是做系統實驗（延遲、快取、降階、弱網）時不必手動重複操作介面。
 *
 * 三個刻意的設計
 * ------------
 * 1. **一次只送一個，等回來才送下一個（序列）。** 同時送多個量到的是伺服器的
 *    排隊時間，不是單一使用者等待的時間；而且 reader 在 8GB 顯卡上並行會搶
 *    記憶體，延遲會失真（見 CLAUDE.md 的 VRAM 說明）。
 * 2. **不寫進查詢歷史。** 歷史是給使用者看的，量測的上百筆會把它洗掉。
 * 3. **間隔從「上一次回來」開始算。** 從送出開始算的話，慢的那幾次後面會接著
 *    立刻送下一次，負載就不是固定的了。
 */
import * as Telemetry from './telemetry';
import { QueryOptions, QueryOutcome, sendAnalysis, toScanRecord } from './queryClient';
import type { PackManifest } from './testPack';
import type { Compressed } from './imageCompress';

export interface BatchOptions {
  /** 送幾次。 */
  count: number;
  /** 上一次回來之後，等多久再送下一次（毫秒）。 */
  intervalMs: number;
  /** 條件標籤，會寫進每一列（例如 wifi、4g、cloud_down）。 */
  label: string;
  query: QueryOptions;
}

export interface BatchProgress {
  runId: string;
  done: number;
  total: number;
  ok: number;
  degraded: number;
  errors: number;
  /** 目前為止成功那幾次的總耗時中位數；還沒有成功的就是 null。 */
  medianMs: number | null;
  lastError: string | null;
}

export interface BatchDeps {
  send?: (q: QueryOptions) => Promise<QueryOutcome>;
  append?: (r: Telemetry.ScanRecord) => Promise<void>;
  sleep?: (ms: number) => Promise<void>;
}

export function newRunId(): string {
  return `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

/** 條件標籤只留安全字元：它會進 CSV，也會被人拿來當檔名。 */
export function cleanLabel(s: string): string {
  return s.trim().replace(/[^A-Za-z0-9_.\-一-鿿]/g, '_').slice(0, 40);
}

const median = (xs: number[]) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
};

/**
 * 跑一批。`shouldStop` 每次送出前檢查一次——按下停止後，**正在跑的那一次
 * 會等它回來並記錄**，不會中途丟掉（那一次的延遲也是有效資料）。
 */
export async function runBatch(
  opts: BatchOptions,
  onProgress: (p: BatchProgress) => void,
  shouldStop: () => boolean,
  deps: BatchDeps = {},
): Promise<BatchProgress> {
  const send = deps.send ?? sendAnalysis;
  const append = deps.append ?? Telemetry.append;
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>(r => setTimeout(r, ms)));
  const runId = newRunId();
  const label = cleanLabel(opts.label);
  const okTimes: number[] = [];
  const p: BatchProgress = {
    runId, done: 0, total: opts.count, ok: 0, degraded: 0, errors: 0, medianMs: null, lastError: null,
  };
  onProgress({ ...p });

  for (let i = 1; i <= opts.count; i++) {
    if (shouldStop()) break;
    const o = await send(opts.query);
    const rec = toScanRecord(o, opts.query, { run_id: runId, run_label: label, seq: i });
    await append(rec);
    p.done = i;
    if (o.kind === 'error') {
      p.errors += 1;
      p.lastError = o.error;
    } else if (o.kind === 'degraded_local' || rec.degraded || rec.offline_mode) {
      // 降階與過期快取不算進中位數：它們走的是另一條路，混進來中位數就不代表任何一種。
      p.degraded += 1;
    } else {
      p.ok += 1;
      okTimes.push(o.totalMs);
    }
    p.medianMs = median(okTimes);
    onProgress({ ...p });
    if (i < opts.count && opts.intervalMs > 0 && !shouldStop()) await sleep(opts.intervalMs);
  }
  return { ...p };
}


// ── 逐案跑完測試集 ─────────────────────────────────────────────────────────

export interface DatasetOptions {
  manifest: PackManifest;
  /** 每一案送幾次。 */
  repeats: number;
  intervalMs: number;
  label: string;
  /** 端點與測試開關；條碼一律留空、只送照片——量的是「拍照辨識」這條路。 */
  query: Omit<QueryOptions, 'barcode' | 'images'>;
}

export interface DatasetDeps extends BatchDeps {
  /** 把測試包裡的一張照片壓成要送出的 base64（正式用 imageCompress + testPack.imageUri）。 */
  compress: (file: string, width: number) => Promise<Compressed | null>;
}

/**
 * 每一案：照片用與真實使用相同的壓縮 → 送出 → 記一列（含 case_id 與壓縮耗時）。
 * **每一次都重新壓縮**，因為真實使用者每次掃描都會經過壓縮，那也是等待的一部分。
 * 壓縮失敗的那一案照樣記一列錯誤，不送出——不記的話那一案會從分母裡消失。
 */
export async function runDataset(
  opts: DatasetOptions,
  onProgress: (p: BatchProgress) => void,
  shouldStop: () => boolean,
  deps: DatasetDeps,
): Promise<BatchProgress> {
  const send = deps.send ?? sendAnalysis;
  const append = deps.append ?? Telemetry.append;
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>(r => setTimeout(r, ms)));
  const runId = newRunId();
  const label = cleanLabel(opts.label);
  const okTimes: number[] = [];
  const total = opts.manifest.cases.length * opts.repeats;
  const p: BatchProgress = {
    runId, done: 0, total, ok: 0, degraded: 0, errors: 0, medianMs: null, lastError: null,
  };
  onProgress({ ...p });

  let seq = 0;
  outer:
  for (const c of opts.manifest.cases) {
    for (let r = 0; r < opts.repeats; r++) {
      if (shouldStop()) break outer;
      seq += 1;
      const images: string[] = [];
      let compressMs = 0;
      let failed: string | null = null;
      for (const im of c.images) {
        try {
          const out = await deps.compress(im.file, im.width);
          if (!out) { failed = `壓縮失敗：${im.file}`; break; }
          images.push(out.base64);
          compressMs += out.ms;
        } catch (e: any) {
          failed = `壓縮失敗：${im.file}（${e?.message ?? e}）`;
          break;
        }
      }
      const query: QueryOptions = { ...opts.query, barcode: '', images };
      const o: QueryOutcome = failed
        ? { kind: 'error', reqId: '', totalMs: 0, httpStatus: null, headers: null, result: null, error: failed }
        : await send(query);
      const rec = toScanRecord(o, query, {
        run_id: runId, run_label: label, seq,
        case_id: c.case_id, pack_version: opts.manifest.pack_version,
        compress_ms: failed ? null : compressMs,
      });
      await append(rec);
      p.done = seq;
      if (o.kind === 'error') {
        p.errors += 1;
        p.lastError = `${c.case_id}：${o.error}`;
      } else if (o.kind === 'degraded_local' || rec.degraded || rec.offline_mode) {
        p.degraded += 1;
      } else {
        p.ok += 1;
        okTimes.push(o.totalMs);
      }
      p.medianMs = median(okTimes);
      onProgress({ ...p });
      if (seq < total && opts.intervalMs > 0 && !shouldStop()) await sleep(opts.intervalMs);
    }
  }
  return { ...p };
}
