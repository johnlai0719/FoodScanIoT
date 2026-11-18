import React from 'react';
import { useBarcodeAvailability } from '../useBarcodeAvailability';
const { create, act } = require('react-test-renderer');
let api: ReturnType<typeof useBarcodeAvailability>;
let renderer: any;
const originalFetch = globalThis.fetch;
const A = '4710063338953', B = '4710000000000';
function Harness({ code = A, enabled = true }: { code?: string; enabled?: boolean }) {
  api = useBarcodeAvailability(code, enabled); return null;
}
const reply = (code: string, exists: boolean) => ({ ok: true,
  json: async () => ({ barcode: code, exists, source: 'cloud_database' }) });
async function mount(code = A) { await act(async () => { renderer = create(<Harness code={code} />); }); }
async function tick() { await act(async () => { jest.advanceTimersByTime(250); }); }
beforeEach(() => {
  jest.useFakeTimers(); (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  globalThis.fetch = jest.fn().mockResolvedValue(reply(A, true));
});
afterEach(async () => { await act(async () => renderer?.unmount()); renderer = undefined;
  jest.useRealTimers(); globalThis.fetch = originalFetch;
});
test.each([true, false])('confirms existence=%s using only a read-only GET', async exists => {
  (fetch as jest.Mock).mockResolvedValue(reply(A, exists));
  await mount(); expect(api.availability.status).toBe('checking'); await tick();
  expect(api.availability.status).toBe(exists ? 'found' : 'missing');
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining(`/${A}/availability`), expect.objectContaining({ method: 'GET' }));
});
test('offline and malformed responses never mean missing', async () => {
  (fetch as jest.Mock).mockRejectedValue(new Error('offline')); await mount(); await tick();
  expect(api.availability.status).toBe('unavailable');
  (fetch as jest.Mock).mockResolvedValue({ ok: true, json: async () => ({ exists: false }) });
  await act(async () => api.refresh()); await tick();
  expect(api.availability.status).toBe('unavailable');
});
test('late reply for an earlier barcode cannot overwrite the current barcode', async () => {
  let finish: (value: any) => void = () => undefined;
  (fetch as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await mount(); await tick();
  await act(async () => renderer.update(<Harness code={B} />));
  (fetch as jest.Mock).mockResolvedValue(reply(B, false)); await tick();
  await act(async () => finish(reply(A, true)));
  expect(api.availability).toEqual({ barcode: B, status: 'missing' });
});
test('partial input and a hidden scan screen do not query', async () => {
  await mount('471'); await tick(); expect(fetch).not.toHaveBeenCalled();
  await act(async () => renderer.update(<Harness enabled={false} />)); await tick();
  expect(fetch).not.toHaveBeenCalled(); expect(api.availability.status).toBe('idle');
});
test('timeout reports unavailable even if the request never resolves', async () => {
  (fetch as jest.Mock).mockImplementation(() => new Promise(() => {}));
  await mount(); await tick();
  await act(async () => jest.advanceTimersByTime(5000));
  expect(api.availability.status).toBe('unavailable');
});
