import fetch from 'node-fetch';
import { getCache, getStaleCache, setCache } from './cache';
import { handleQuery } from './queryHandler';

jest.mock('node-fetch', () => ({ __esModule: true, default: jest.fn() }));
jest.mock('./cache', () => ({ getCache: jest.fn(), getStaleCache: jest.fn(), setCache: jest.fn() }));
const mockedFetch = fetch as jest.MockedFunction<typeof fetch>;
const respond = (status: number, body: object) => ({
  ok: status >= 200 && status < 300, status,
  headers: { get: () => null }, json: async () => body,
});

beforeEach(() => { jest.resetAllMocks(); });

test('passes durable queue acknowledgement through without caching it', async () => {
  mockedFetch.mockResolvedValue(respond(202, { status: 'queued', queue_id: 'abc' }) as any);
  const result = await handleQuery({ barcode: '471', label_images: ['photo'] });
  expect(result.status).toBe(202);
  expect(result.cacheHeader).toBe('QUEUED');
  expect(result.data.queue_id).toBe('abc');
  expect(setCache).not.toHaveBeenCalled();
});

test('normalization failure still serves SQLite cache and does not upload again', async () => {
  (getCache as jest.Mock).mockReturnValue({ status: 'success', health_score: 80 });
  mockedFetch.mockResolvedValue(respond(503, {}) as any);
  const result = await handleQuery({ barcode: '471' });
  expect(result.data.health_score).toBe(80);
  expect(result.data.cached).toBe(true);
  expect(mockedFetch).toHaveBeenCalledTimes(1);
});

test('Python outage never pretends data was queued', async () => {
  mockedFetch.mockRejectedValue(new Error('offline'));
  const result = await handleQuery({ barcode: '471' });
  expect(result.status).toBe(503);
  expect(result.data.queued).toBe(false);
});

test('TEST pictures do not reuse another picture result', async () => {
  (getCache as jest.Mock).mockReturnValue({ status: 'success', health_score: 80 });
  mockedFetch.mockResolvedValue(respond(200, { status: 'success', health_score: 90 }) as any);
  const result = await handleQuery({ barcode: 'TEST', label_images: ['other'] });
  expect(getCache).not.toHaveBeenCalled();
  expect(getStaleCache).not.toHaveBeenCalled();
  expect(setCache).not.toHaveBeenCalled();
  expect(result.data.health_score).toBe(90);
});

test('cache bypass skips both reads but still stores a fresh Cloud result', async () => {
  mockedFetch.mockResolvedValue(respond(200, { status: 'success', health_score: 90 }) as any);
  const result = await handleQuery({ barcode: '471' }, 'bypass-test', true);
  expect(getCache).not.toHaveBeenCalled();
  expect(getStaleCache).not.toHaveBeenCalled();
  expect(setCache).toHaveBeenCalledWith('471', expect.objectContaining({ health_score: 90 }));
  expect(result.cacheHeader).toBe('BYPASS');
  expect(mockedFetch.mock.calls[0][1]?.headers).toEqual(expect.objectContaining({ 'X-Bypass-Cache': '1' }));
});
