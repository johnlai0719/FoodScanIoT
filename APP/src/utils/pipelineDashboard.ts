/** Optional competition observer. No profile data or API credentials are sent. */
const BASE = (process.env.EXPO_PUBLIC_PIPELINE_DASHBOARD_URL || '').replace(/\/$/, '');
type Stage = 'image' | 'sync' | 'error';

export async function pipelineEvent(
  requestId: string, stage: Stage, status: 'active' | 'done' | 'failed',
  payload: Record<string, unknown> = {},
): Promise<void> {
  if (!BASE) return;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1500);
  try {
    await fetch(`${BASE}/api/events`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: requestId, stage, status, sender: 'app', payload }),
      signal: controller.signal,
    });
  } catch { /* Observer outages do not interrupt scanning. */ }
  finally { clearTimeout(timer); }
}
