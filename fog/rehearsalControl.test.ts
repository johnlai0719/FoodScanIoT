jest.mock('node-fetch', () => ({ __esModule: true, default: jest.fn() }));
jest.mock('./cache', () => ({ deleteCache: jest.fn() }));
import fetch from 'node-fetch';
import { deleteCache } from './cache';
import { claimNextMiss } from './rehearsalControl';

const mockedFetch = fetch as jest.MockedFunction<typeof fetch>;
beforeEach(() => {
  jest.clearAllMocks();
  process.env.FOG_DEMO_CONTROL_ENABLED = '1';
  process.env.FOG_DEMO_CONTROL_TOKEN = 'test-only';
  process.env.PIPELINE_DASHBOARD_URL = 'http://dashboard.test';
});
afterEach(() => { delete process.env.FOG_DEMO_CONTROL_ENABLED; delete process.env.FOG_DEMO_CONTROL_TOKEN; delete process.env.PIPELINE_DASHBOARD_URL; });

test('clears actual SQLite cache before acknowledging application', async () => {
  mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ clear_cache: true, barcode: '4710000000001', task_id: 'test-task' }) } as any)
    .mockResolvedValueOnce({ ok: true } as any);
  expect(await claimNextMiss('4710000000001', 'rid')).toBe(true);
  expect(deleteCache).toHaveBeenCalledWith('4710000000001');
  expect((deleteCache as jest.Mock).mock.invocationCallOrder[0]).toBeLessThan(mockedFetch.mock.invocationCallOrder[1]);
});
test('unarmed, observer failure and TEST never clear a cache or pretend a Miss', async () => {
  mockedFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ applied: false }) } as any);
  expect(await claimNextMiss('4710000000001', 'rid')).toBe(false);
  mockedFetch.mockRejectedValueOnce(Error('offline'));
  expect(await claimNextMiss('4710000000001', 'rid2')).toBe(false);
  expect(await claimNextMiss('TEST', 'rid3')).toBe(false);
  expect(deleteCache).not.toHaveBeenCalled();
});
