import { useCallback, useEffect, useState } from 'react';
import { PRODUCT_AVAILABILITY_URL } from '../constants/endpoints';

export type BarcodeAvailability = {
  barcode: string;
  status: 'idle' | 'checking' | 'found' | 'missing' | 'unavailable';
};

export function useBarcodeAvailability(barcode: string, enabled: boolean) {
  const code = barcode.trim();
  const valid = /^[0-9]{8,14}$/.test(code);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<BarcodeAvailability>({ barcode: '', status: 'idle' });
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    if (!enabled || !valid) return;
    let active = true;
    const controller = new AbortController();
    let deadline: ReturnType<typeof setTimeout> | undefined;
    setState({ barcode: code, status: 'checking' });
    // Debounce manual entry and cancel earlier lookups when another barcode is scanned.
    const timer = setTimeout(async () => {
      deadline = setTimeout(() => {
        controller.abort();
        if (active) setState({ barcode: code, status: 'unavailable' });
      }, 5000);
      try {
        const response = await fetch(`${PRODUCT_AVAILABILITY_URL}/${code}/availability`, {
          method: 'GET', headers: { 'Cache-Control': 'no-cache' }, signal: controller.signal,
        });
        if (!response.ok) throw new Error('Availability unavailable');
        const result = await response.json();
        if (result.barcode !== code || typeof result.exists !== 'boolean' || result.source !== 'cloud_database') {
          throw new Error('Invalid availability response');
        }
        if (active && !controller.signal.aborted) setState({ barcode: code, status: result.exists ? 'found' : 'missing' });
      } catch {
        if (active) setState({ barcode: code, status: 'unavailable' });
      } finally {
        clearTimeout(deadline);
      }
    }, 250);
    return () => { active = false; clearTimeout(timer); clearTimeout(deadline); controller.abort(); };
  }, [code, enabled, valid, revision]);

  const availability: BarcodeAvailability = !enabled || !valid
    ? { barcode: code, status: 'idle' }
    : state.barcode !== code ? { barcode: code, status: 'checking' } : state;
  return { availability, refresh };
}
