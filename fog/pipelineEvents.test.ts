import fetch from 'node-fetch';
import { emitFog, resultSummary } from './pipelineEvents';
jest.mock('node-fetch', () => ({ __esModule: true, default: jest.fn() }));
const mockFetch = fetch as jest.Mock;
const originalUrl = process.env.PIPELINE_DASHBOARD_URL;
afterEach(() => {
  if (originalUrl === undefined) delete process.env.PIPELINE_DASHBOARD_URL;
  else process.env.PIPELINE_DASHBOARD_URL = originalUrl;
  jest.resetAllMocks();
});
test('disabled observer does no network I/O', () => {
  delete process.env.PIPELINE_DASHBOARD_URL;
  emitFog('scan', 'cache_hit', 'active');
  expect(mockFetch).not.toHaveBeenCalled();
});
test('observer failure is absorbed and only safe metadata is reported', async () => {
  process.env.PIPELINE_DASHBOARD_URL = 'http://observer.test';
  mockFetch.mockRejectedValue(new Error('offline'));
  const raw = { status: 'success', health_score: 0, product_info: { name: 'example' },
    label_images: ['private-photo'], user_conditions: { group: 'child' }, secret: 'private-secret' };
  expect(() => emitFog('scan', 'cache_returned', 'done', { cache_status: 'HIT' }, resultSummary(raw))).not.toThrow();
  const body = JSON.parse(mockFetch.mock.calls[0][1].body);
  expect(body.sender).toBe('fog');
  expect(body.payload.result.health_score).toBe(0);
  expect(JSON.stringify(body)).not.toMatch(/private-photo|private-secret|user_conditions/);
  await new Promise(resolve => setTimeout(resolve, 0));
});
