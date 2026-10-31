import fetch from 'node-fetch';
import { randomUUID } from 'crypto';
import { deleteCache } from './cache';

// One-shot dashboard command is claimed BEFORE either SQLite cache lookup.
// Only explicitly enabled competition nodes use this authenticated control API.
export async function claimNextMiss(barcode: string, requestId: string): Promise<boolean> {
  const base = process.env.PIPELINE_DASHBOARD_URL?.replace(/\/$/, '');
  const token = process.env.FOG_DEMO_CONTROL_TOKEN;
  if (process.env.FOG_DEMO_CONTROL_ENABLED !== '1' || !base || !token || !/^\d{8,14}$/.test(barcode)) return false;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  const claimId = requestId || randomUUID();
  const fogId = process.env.FOG_DEMO_NODE_ID || 'foodscan-pi';
  try {
    const response = await fetch(base + '/api/rehearsal/claim', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Fog-Control-Key': token },
      body: JSON.stringify({ barcode, request_id: claimId, fog_id: fogId }), signal: controller.signal,
    });
    if (!response.ok) return false;
    const result = await response.json() as { clear_cache?: boolean; barcode?: string; task_id?: string };
    if (result.clear_cache !== true || result.barcode !== barcode || !result.task_id) return false;
    // Node and Python share this SQLite file. The delete is synchronous and
    // finishes before handleQuery can read either fresh or stale cache.
    deleteCache(barcode);
    await fetch(base + '/api/rehearsal/applied', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Fog-Control-Key': token },
      body: JSON.stringify({ barcode, request_id: claimId, fog_id: fogId, task_id: result.task_id, applied: true }), signal: controller.signal,
    });
    return true;
  } catch {
    // An unavailable observer never stops normal scans or pretends a Miss occurred.
    return false;
  } finally {
    clearTimeout(timeout);
  }
}
