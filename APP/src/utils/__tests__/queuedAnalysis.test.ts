import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  QueuedAnalysis, saveQueuedAnalysis, loadQueuedAnalyses, removeQueuedAnalysis,
  queuedElapsedMs, responseBody, isAnalysisResult,
} from '../queuedAnalysis';

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true, default: { getItem: jest.fn(), setItem: jest.fn() },
}));
let saved: string | null;
const task = (id = 'a'.repeat(32)): QueuedAnalysis => ({
  id, requestId: 'app-original', endpoint: 'http://fog.test:3001', submittedAt: 1000,
  barcode: '471', nImages: 1, payloadChars: 400, bypassCache: false, message: '已暫存',
});
beforeEach(() => {
  saved = null;
  (AsyncStorage.getItem as jest.Mock).mockImplementation(async () => saved);
  (AsyncStorage.setItem as jest.Mock).mockImplementation(async (_key, value) => { saved = value; });
});

test('keeps concurrent tasks and persists no photos, profile or result', async () => {
  await Promise.all([
    saveQueuedAnalysis({ ...task(), label_images: ['private-photo'], user_conditions: { group: 'child' } } as any),
    saveQueuedAnalysis(task('b'.repeat(32))),
  ]);
  expect(await loadQueuedAnalyses()).toHaveLength(2);
  expect(saved).not.toMatch(/private-photo|user_conditions|label_images/);
  await removeQueuedAnalysis(task());
  expect((await loadQueuedAnalyses()).map(t => t.id)).toEqual(['b'.repeat(32)]);
});
test('duplicate acknowledgement preserves original local submission time', async () => {
  await saveQueuedAnalysis(task());
  await saveQueuedAnalysis({ ...task(), submittedAt: 9000, requestId: 'app-duplicate' });
  expect(await loadQueuedAnalyses()).toEqual([task()]);
  expect(queuedElapsedMs(task(), 61000)).toBe(60000);
  expect(queuedElapsedMs(task(), 900)).toBeNull();
});
test('storage failure is exposed instead of silently claiming persistence', async () => {
  (AsyncStorage.setItem as jest.Mock).mockRejectedValueOnce(new Error('disk full'));
  await expect(saveQueuedAnalysis(task())).rejects.toThrow('disk full');
  expect(await loadQueuedAnalyses()).toEqual([]);
  await saveQueuedAnalysis(task());
  expect(await loadQueuedAnalyses()).toHaveLength(1);
});
test('keeps server explanation for a non-successful HTTP response', async () => {
  await expect(responseBody({ ok: false, status: 503,
    json: async () => ({ message: '未暫存，請保留照片' }) } as Response))
    .rejects.toMatchObject({ message: '未暫存，請保留照片', httpStatus: 503 });
});
test('missing score never becomes a default score; zero and negative scores remain valid', () => {
  expect(isAnalysisResult({ health_score: null })).toBe(false);
  expect(isAnalysisResult({ health_score: NaN })).toBe(false);
  expect(isAnalysisResult({ status: 'queued', health_score: 80 })).toBe(false);
  expect(isAnalysisResult({ status: 'success', health_score: 0 })).toBe(true);
  expect(isAnalysisResult({ health_score: -5 })).toBe(true);
});

test('shows the Fog validator error field instead of a generic HTTP 400', async () => {
  await expect(responseBody({ ok: false, status: 400,
    json: async () => ({ error: '無效的條碼格式，須為 8-13 位數字或測試字串', code: 400 }) } as Response))
    .rejects.toMatchObject({ message: '無效的條碼格式，須為 8-13 位數字或測試字串', httpStatus: 400 });
});
