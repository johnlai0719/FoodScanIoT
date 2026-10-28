import fetch from 'node-fetch';

export type FogEvent = 'received' | 'cache_hit' | 'cache_miss' | 'cache_bypass' | 'cache_excluded'
  | 'cache_returned' | 'manager_forwarded' | 'manager_returned' | 'queued' | 'unavailable';

/** Optional observer; no images, profiles, credentials or raw exception text are transmitted. */
export function emitFog(requestId: string, event: FogEvent, status: 'active' | 'done' | 'failed',
  detail: Record<string, unknown> = {}, payload: Record<string, unknown> = {}): void {
  const base = (process.env.PIPELINE_DASHBOARD_URL || '').replace(/\/$/, '');
  if (!base || !/^[A-Za-z0-9_.:-]{1,96}$/.test(requestId)) return;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 700);
  timer.unref?.();
  void fetch(`${base}/api/events`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: requestId, stage: 'fog', sender: 'fog', status,
      fog: { event, layer: 'node', ...detail }, payload }), signal: controller.signal,
  }).catch(() => undefined).finally(() => clearTimeout(timer));
}

export function resultSummary(result: any) {
  return result?.status === 'success' && Number.isFinite(result.health_score)
    ? { result: { product_name: result.product_info?.name, health_score: result.health_score } } : {};
}
