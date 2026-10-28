import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useQueuedAnalysis } from '../useQueuedAnalysis';
import { QueuedAnalysis } from '../../utils/queuedAnalysis';
const { create, act } = require('react-test-renderer');

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true, default: { getItem: jest.fn(), setItem: jest.fn() },
}));
const task: QueuedAnalysis = {
  id: 'a'.repeat(32), requestId: 'app-original', endpoint: 'http://old-fog.test:3001',
  submittedAt: 1000, barcode: 'TEST', nImages: 1, payloadChars: 200, bypassCache: false, message: '已暫存',
};
let saved: string | null;
let api: ReturnType<typeof useQueuedAnalysis>;
let renderer: any;
const callbacks = { restored: jest.fn(), completed: jest.fn(), failed: jest.fn() };
const originalFetch = globalThis.fetch;
function Harness() { api = useQueuedAnalysis(callbacks); return null; }
async function mount() { await act(async () => { renderer = create(<Harness />); }); }
async function unmount() { await act(async () => renderer?.unmount()); renderer = undefined; }
beforeEach(() => {
  jest.useFakeTimers();
  saved = JSON.stringify([task]);
  jest.clearAllMocks();
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  (AsyncStorage.getItem as jest.Mock).mockImplementation(async () => saved);
  (AsyncStorage.setItem as jest.Mock).mockImplementation(async (_key, value) => { saved = value; });
  globalThis.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ state: 'pending' }) });
});
afterEach(async () => { await unmount(); jest.useRealTimers(); globalThis.fetch = originalFetch; });

test('restores after unmount/remount and queries the saved Fog, not a new default', async () => {
  await mount();
  expect(callbacks.restored).toHaveBeenCalledTimes(1);
  expect(globalThis.fetch).toHaveBeenCalledWith(`${task.endpoint}/queue/${task.id}`, expect.any(Object));
  await unmount();
  (globalThis.fetch as jest.Mock).mockResolvedValue({ ok: true, status: 200,
    json: async () => ({ state: 'completed', request_id: 'app-original', result: { health_score: 0 } }) });
  await mount();
  expect(callbacks.completed).toHaveBeenCalledWith(task, expect.objectContaining({ state: 'completed' }));
  // A crash before displaying the result must still leave a recoverable task.
  expect(JSON.parse(saved!)).toHaveLength(1);
  await act(async () => { await api.dismiss(task); });
  expect(JSON.parse(saved!)).toEqual([]);
});
test('transient outage retries without overlapping requests, 404 stops and retains record', async () => {
  (globalThis.fetch as jest.Mock).mockRejectedValueOnce(new Error('offline'));
  await mount();
  expect(api.notice).toContain('自動重試');
  expect(callbacks.failed).not.toHaveBeenCalled();
  (globalThis.fetch as jest.Mock).mockResolvedValue({ ok: false, status: 404,
    json: async () => ({ detail: 'not found' }) });
  await act(async () => { jest.advanceTimersByTime(10000); });
  expect(callbacks.failed).toHaveBeenCalledWith(task, expect.stringContaining('查無此任務'));
  expect(JSON.parse(saved!)).toHaveLength(1);
  const count = (globalThis.fetch as jest.Mock).mock.calls.length;
  await act(async () => { jest.advanceTimersByTime(60000); });
  expect(globalThis.fetch).toHaveBeenCalledTimes(count);
});
test('new scan can pause polling without deleting earlier tasks', async () => {
  await mount();
  await act(async () => api.pause());
  expect(api.current).toBeNull();
  expect(JSON.parse(saved!)).toHaveLength(1);
  await act(async () => { await api.remember({ ...task, id: 'b'.repeat(32) }); });
  expect(JSON.parse(saved!)).toHaveLength(2);
});
test('duplicate task keeps the first submission time in the active poll too', async () => {
  await mount();
  await act(async () => { await api.remember({ ...task, submittedAt: 9000 }); });
  expect(api.current?.submittedAt).toBe(1000);
});
test('late response after unmount never delivers a completed result', async () => {
  let finish: (value: any) => void = () => undefined;
  (globalThis.fetch as jest.Mock).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await mount();
  await unmount();
  await act(async () => { finish({ ok: true, json: async () => ({ state: 'completed' }) }); });
  expect(callbacks.completed).not.toHaveBeenCalled();
});
