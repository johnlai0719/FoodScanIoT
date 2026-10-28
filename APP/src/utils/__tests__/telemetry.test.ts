import AsyncStorage from '@react-native-async-storage/async-storage';
import { append, clear, load, ScanRecord, toCSV } from '../telemetry';

jest.mock('@react-native-async-storage/async-storage', () => {
  let stored: string | null = null;
  return {
    getItem: jest.fn(async () => stored),
    setItem: jest.fn(async (_key: string, value: string) => { stored = value; }),
    removeItem: jest.fn(async () => { stored = null; }),
  };
});

it('preserves concurrent acknowledgement and terminal events and orders clear', async () => {
  await clear();
  const base = { request_id: 'app-one', total_ms: 10 } as ScanRecord;
  await Promise.all([
    append({ ...base, phase: 'response' }),
    append({ ...base, phase: 'queue_completed', total_ms: 12000 }),
  ]);
  const records = await load();
  expect(records.map(r => r.phase)).toEqual(['response', 'queue_completed']);
  expect(toCSV(records)).toContain('request_id,phase,');
  await Promise.all([append(base), clear()]);
  expect(await load()).toEqual([]);
  expect(AsyncStorage.removeItem).toHaveBeenCalled();
});
