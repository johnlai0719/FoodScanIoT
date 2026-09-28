import { runBatch, cleanLabel } from '../batchRunner';
import { sendAnalysis, toScanRecord, QueryOptions, QueryOutcome } from '../queryClient';
import { ScanRecord, toCSV } from '../telemetry';

const query: QueryOptions = {
  endpoint: 'fog', barcode: '4710018123456', images: ['aaaa', 'bb'], bypassCache: true, localOnly: false,
};

function outcome(kind: QueryOutcome['kind'], totalMs: number, result: any = null, error: string | null = null): QueryOutcome {
  return { kind, reqId: `r${totalMs}`, totalMs, httpStatus: kind === 'error' ? null : 200, headers: null, result, error };
}

function fakeResponse(body: any, headers: Record<string, string> = {}, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k: string) => headers[k] ?? null } as any,
    json: async () => body,
  } as any;
}

describe('runBatch', () => {
  it('依序送出指定次數，每一列都帶 run_id／標籤／序號，且不寫歷史以外的東西', async () => {
    const seq = [outcome('ok', 3000, { health_score: 80 }), outcome('ok', 1000, { health_score: 80 }),
                 outcome('error', 500, null, '伺服器代碼: 502')];
    const records: ScanRecord[] = [];
    const sleeps: number[] = [];
    const final = await runBatch(
      { count: 3, intervalMs: 2000, label: 'wifi', query },
      () => {}, () => false,
      { send: async () => seq.shift()!, append: async r => { records.push(r); }, sleep: async ms => { sleeps.push(ms); } },
    );
    expect(records).toHaveLength(3);
    expect(records.map(r => r.seq)).toEqual([1, 2, 3]);
    expect(new Set(records.map(r => r.run_id)).size).toBe(1);
    expect(records.every(r => r.run_label === 'wifi')).toBe(true);
    expect(final).toMatchObject({ done: 3, ok: 2, errors: 1, degraded: 0, lastError: '伺服器代碼: 502' });
    // 中位數只算成功的那兩次
    expect(final.medianMs).toBe(3000);
    // 間隔只出現在兩次之間，最後一次之後不等
    expect(sleeps).toEqual([2000, 2000]);
  });

  it('按下停止後不再送新的，但已送出的那一次照常記錄', async () => {
    let stop = false;
    const records: ScanRecord[] = [];
    const final = await runBatch(
      { count: 10, intervalMs: 0, label: 'x', query },
      () => {}, () => stop,
      { send: async () => { stop = true; return outcome('ok', 100, { health_score: 1 }); },
        append: async r => { records.push(r); } },
    );
    expect(final.done).toBe(1);
    expect(records).toHaveLength(1);
  });

  it('降階與過期快取分開計數，也不算進中位數', async () => {
    const seq = [
      outcome('degraded_local', 800, { status: 'degraded', degraded_mode: 'local_ocr' }),
      outcome('ok', 50, { health_score: 70, _offline_mode: true }),
      outcome('ok', 9000, { health_score: 70 }),
    ];
    const records: ScanRecord[] = [];
    const final = await runBatch(
      { count: 3, intervalMs: 0, label: 'cloud_down', query }, () => {}, () => false,
      { send: async () => seq.shift()!, append: async r => { records.push(r); } },
    );
    expect(final).toMatchObject({ ok: 1, degraded: 2, errors: 0, medianMs: 9000 });
    expect(records.map(r => r.offline_mode)).toEqual([false, true, false]);
    expect(records[0].degraded).toBe(true);
  });
});

describe('cleanLabel', () => {
  it('只留安全字元，保留中文', () => {
    expect(cleanLabel(' 4g, 地下室 ')).toBe('4g__地下室');
    expect(cleanLabel('a'.repeat(60))).toHaveLength(40);
  });
});

describe('sendAnalysis', () => {
  it('帶上測試標頭與 request id，成功時回 ok', async () => {
    let sent: any = null;
    const o = await sendAnalysis({
      ...query,
      fetchImpl: (async (_url: string, init: any) => { sent = init; return fakeResponse({ health_score: 88 }, { 'X-Cache': 'BYPASS' }); }) as any,
    });
    expect(o.kind).toBe('ok');
    expect(sent.headers['X-Bypass-Cache']).toBe('1');
    expect(sent.headers['X-Local-Only']).toBeUndefined();
    expect(sent.headers['X-Request-Id']).toBe(o.reqId);
    expect(JSON.parse(sent.body)).toEqual({ barcode: query.barcode, label_images: query.images });
  });

  it('HTTP 200 但沒有 health_score 的是失敗，訊息取後端寫好的', async () => {
    const o = await sendAnalysis({
      ...query, fetchImpl: (async () => fakeResponse({ status: 'rejected', message: '請對準成分表' })) as any,
    });
    expect(o).toMatchObject({ kind: 'error', error: '請對準成分表', httpStatus: 200 });
  });

  it('Fog 本機降階是部分結果，不是失敗', async () => {
    const o = await sendAnalysis({
      ...query, fetchImpl: (async () => fakeResponse({ status: 'degraded', degraded_mode: 'local_ocr' })) as any,
    });
    expect(o.kind).toBe('degraded_local');
  });

  it('連不上時回 error，不丟例外', async () => {
    const o = await sendAnalysis({ ...query, fetchImpl: (async () => { throw new Error('Network request failed'); }) as any });
    expect(o).toMatchObject({ kind: 'error', error: 'Network request failed', httpStatus: null });
  });
});

describe('toScanRecord／toCSV', () => {
  it('記錄計時標頭、測試開關與批次欄位，並出現在 CSV 欄位中', () => {
    const headers = { get: (k: string) => ({ 'X-Timing-Cloud': 'vision=10412;total=13150', 'X-Cache': 'BYPASS' } as any)[k] ?? null };
    const rec = toScanRecord(
      { kind: 'ok', reqId: 'app-1', totalMs: 14000, httpStatus: 200, headers: headers as any,
        result: { health_score: 60, ingredients_detail: [1, 2, 3] }, error: null },
      { ...query, localOnly: true }, { run_id: 'run-a', run_label: '4g', seq: 7 },
    );
    expect(rec).toMatchObject({
      cloud: { vision: 10412, total: 13150 }, cache: 'BYPASS', local_only: true, bypass_cache: true,
      n_images: 2, payload_chars: 6, n_additives: 3, run_label: '4g', seq: 7, offline_mode: false,
    });
    const csv = toCSV([rec]);
    const [head, row] = csv.split('\n');
    const cols = head.split(',');
    for (const c of ['run_id', 'run_label', 'seq', 'local_only', 'offline_mode', 'cloud_vision']) {
      expect(cols).toContain(c);
    }
    expect(row.split(',')[cols.indexOf('run_label')]).toBe('4g');
  });
});
