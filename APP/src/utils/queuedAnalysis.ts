import AsyncStorage from '@react-native-async-storage/async-storage';
import { AnalysisResponse } from '../types';

const KEY = '@foodscan/queued-analysis/v1';
export interface QueuedAnalysis {
  id: string;
  requestId: string;
  endpoint: string;
  submittedAt: number;
  barcode: string;
  nImages: number;
  payloadChars: number;
  bypassCache: boolean;
  message: string;
}
export interface QueueJob {
  state: 'pending' | 'processing' | 'completed' | 'failed';
  request_id?: string;
  result?: unknown;
}
export const taskKey = (task: QueuedAnalysis) => `${task.endpoint}|${task.id}`;
let writes: Promise<unknown> = Promise.resolve();

function validTask(value: any): value is QueuedAnalysis {
  if (!value || !/^[a-f0-9]{32}$/.test(value.id) || typeof value.endpoint !== 'string') return false;
  try {
    const url = new URL(value.endpoint);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return false;
  } catch { return false; }
  return typeof value.requestId === 'string' && /^[A-Za-z0-9_.:-]{1,96}$/.test(value.requestId)
    && Number.isFinite(value.submittedAt) && value.submittedAt > 0
    && typeof value.barcode === 'string' && Number.isInteger(value.nImages) && value.nImages >= 0
    && Number.isFinite(value.payloadChars) && value.payloadChars >= 0 && typeof value.bypassCache === 'boolean'
    && typeof value.message === 'string';
}
async function read(): Promise<QueuedAnalysis[]> {
  const raw = await AsyncStorage.getItem(KEY);
  if (!raw) return [];
  const data: unknown = JSON.parse(raw);
  if (!Array.isArray(data) || !data.every(validTask)) throw new Error('手機任務紀錄格式異常，請保留任務編號。');
  return data;
}
export async function loadQueuedAnalyses(): Promise<QueuedAnalysis[]> {
  await writes;
  return read();
}
function change(update: (tasks: QueuedAnalysis[]) => QueuedAnalysis[]): Promise<QueuedAnalysis[]> {
  const operation = writes.then(async () => {
    const tasks = update(await read());
    // Only persist task metadata; never persist uploaded photos, health profiles or results.
    const clean = tasks.map(({ id, requestId, endpoint, submittedAt, barcode, nImages, payloadChars, bypassCache, message }) =>
      ({ id, requestId, endpoint, submittedAt, barcode, nImages, payloadChars, bypassCache, message }));
    await AsyncStorage.setItem(KEY, JSON.stringify(clean));
    return clean;
  });
  writes = operation.catch(() => undefined);
  return operation;
}
export function saveQueuedAnalysis(task: QueuedAnalysis): Promise<QueuedAnalysis[]> {
  if (!validTask(task)) return Promise.reject(new Error('任務資料不完整，請保留 Fog 任務編號。'));
  return change(tasks => {
    const existing = tasks.find(t => taskKey(t) === taskKey(task));
    return existing ? tasks : [...tasks, task];
  });
}
export function removeQueuedAnalysis(task: QueuedAnalysis): Promise<QueuedAnalysis[]> {
  return change(tasks => tasks.filter(t => taskKey(t) !== taskKey(task)));
}
export function queuedElapsedMs(task: QueuedAnalysis, now = Date.now()): number | null {
  return now >= task.submittedAt ? now - task.submittedAt : null;
}
export async function responseBody(response: Response): Promise<any> {
  let body: any = null;
  try { body = await response.json(); } catch { /* Fall back to a status-specific message. */ }
  if (!response.ok) {
    const message = typeof body?.message === 'string' ? body.message
      : typeof body?.detail === 'string' ? body.detail
      : typeof body?.error === 'string' ? body.error : `伺服器代碼: ${response.status}`;
    throw Object.assign(new Error(message), { httpStatus: response.status });
  }
  if (!body || typeof body !== 'object') throw new Error('伺服器回應格式異常，請稍後再試。');
  return body;
}
export async function pollQueuedAnalysis(task: QueuedAnalysis, signal: AbortSignal): Promise<QueueJob> {
  const response = await fetch(`${task.endpoint}/queue/${task.id}`, { signal });
  const body = await responseBody(response);
  if (!['pending', 'processing', 'completed', 'failed'].includes(body.state)) {
    throw new Error('Fog 任務狀態格式異常，將稍後重新查詢。');
  }
  return body as QueueJob;
}
export function isAnalysisResult(result: any): result is AnalysisResponse {
  return result && typeof result.health_score === 'number' && Number.isFinite(result.health_score)
    && (!result.status || result.status === 'success');
}
