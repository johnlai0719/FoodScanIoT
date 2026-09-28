jest.mock('@react-native-async-storage/async-storage', () => {
  const store: Record<string, string> = {};
  return {
    getItem: jest.fn(async (k: string) => store[k] ?? null),
    setItem: jest.fn(async (k: string, v: string) => { store[k] = v; }),
  };
});
jest.mock('expo-network', () => ({ getIpAddressAsync: jest.fn(async () => '0.0.0.0') }));

import {
  lanCandidates, normalizeBase, probeFog, scanForFog, resolveFog, setFogBase, getFogBase,
  getFogQueryUrl, DEFAULT_FOG_BASE,
} from '../fogAddress';

/** 假的網路：只有 fogs 裡的位址會回 Fog 的 /health，其餘一律連不上。 */
function fakeNet(fogs: Record<string, any>, other: Record<string, any> = {}) {
  const calls: string[] = [];
  const impl = (async (url: string) => {
    calls.push(url);
    const base = url.replace(/\/health$/, '');
    const body = fogs[base] ?? other[base];
    if (!body) throw new Error('Network request failed');
    return { ok: true, status: 200, json: async () => body } as any;
  }) as any;
  return { impl, calls };
}

describe('lanCandidates', () => {
  it('私有網段列出同一個 /24 的其他 253 台', () => {
    const c = lanCandidates('192.168.1.23');
    expect(c).toHaveLength(253);
    expect(c[0]).toBe('http://192.168.1.1:3001');
    expect(c).not.toContain('http://192.168.1.23:3001');
  });
  it('行動網路、Tailscale 的 100.x、IPv6 不掃', () => {
    expect(lanCandidates('100.86.249.10')).toEqual([]);
    expect(lanCandidates('36.228.1.2')).toEqual([]);
    expect(lanCandidates('fe80::1')).toEqual([]);
    expect(lanCandidates('172.20.0.5')).toHaveLength(253);
    expect(lanCandidates('10.0.0.9')).toHaveLength(253);
  });
});

describe('normalizeBase', () => {
  it('補上 http:// 與預設埠，去掉 /query 與結尾斜線', () => {
    expect(normalizeBase('192.168.1.50')).toBe('http://192.168.1.50:3001');
    expect(normalizeBase('http://192.168.1.50:3001/query/')).toBe('http://192.168.1.50:3001');
    expect(normalizeBase(' 10.0.0.2:8080 ')).toBe('http://10.0.0.2:8080');
  });
});

describe('probeFog', () => {
  it('認得舊版（layer）與新版（service）的 Fog，不認其他服務', async () => {
    const { impl } = fakeNet(
      { 'http://a:3001': { layer: 'fog-node', commit: 'abc' },
        'http://b:3001': { service: 'foodscan-fog', name: 'lab-pi' } },
      { 'http://c:3001': { status: 'ok' } },
    );
    expect(await probeFog('http://a:3001', 100, impl)).toMatchObject({ commit: 'abc', name: null });
    expect(await probeFog('http://b:3001', 100, impl)).toMatchObject({ name: 'lab-pi' });
    expect(await probeFog('http://c:3001', 100, impl)).toBeNull();
    expect(await probeFog('http://d:3001', 100, impl)).toBeNull();
  });
});

describe('scanForFog', () => {
  it('在候選清單中找到 Fog', async () => {
    const cands = lanCandidates('192.168.1.23');
    const { impl } = fakeNet({ 'http://192.168.1.200:3001': { layer: 'fog-node' } });
    const f = await scanForFog(cands, { fetchImpl: impl, timeoutMs: 50 });
    expect(f?.base).toBe('http://192.168.1.200:3001');
  });
  it('都不是就回 null', async () => {
    const { impl } = fakeNet({});
    expect(await scanForFog(lanCandidates('192.168.1.23'), { fetchImpl: impl, timeoutMs: 50 })).toBeNull();
  });
});

describe('resolveFog', () => {
  beforeEach(async () => { await setFogBase(DEFAULT_FOG_BASE); });

  it('上次的位址還通就直接用，不掃描', async () => {
    await setFogBase('http://192.168.1.200:3001');
    const { impl, calls } = fakeNet({ 'http://192.168.1.200:3001': { layer: 'fog-node' } });
    const r = await resolveFog({ fetchImpl: impl, getIp: async () => '192.168.1.23' });
    expect(r.source).toBe('saved');
    expect(calls).toHaveLength(1);
  });

  it('上次的位址不通就掃區網，找到後記住並用於查詢', async () => {
    await setFogBase('http://10.9.9.9:3001');
    const { impl } = fakeNet({ 'http://192.168.0.77:3001': { layer: 'fog-node' } });
    const r = await resolveFog({ fetchImpl: impl, getIp: async () => '192.168.0.5' });
    expect(r.source).toBe('lan');
    expect(getFogBase()).toBe('http://192.168.0.77:3001');
    expect(getFogQueryUrl()).toBe('http://192.168.0.77:3001/query');
  });

  it('區網找不到就退回預設（Tailscale）', async () => {
    await setFogBase('http://10.9.9.9:3001');
    const { impl } = fakeNet({ [DEFAULT_FOG_BASE]: { layer: 'fog-node' } });
    const r = await resolveFog({ fetchImpl: impl, getIp: async () => '36.228.1.2' });
    expect(r.source).toBe('default');
    expect(getFogBase()).toBe(DEFAULT_FOG_BASE);
  });

  it('全部都找不到時不動目前的位址', async () => {
    await setFogBase('http://10.9.9.9:3001');
    const { impl } = fakeNet({});
    const r = await resolveFog({ fetchImpl: impl, getIp: async () => '36.228.1.2' });
    expect(r).toEqual({ info: null, source: 'none' });
    expect(getFogBase()).toBe('http://10.9.9.9:3001');
  });
});
