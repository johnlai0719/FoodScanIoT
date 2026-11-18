/**
 * Fog 在哪裡：記住上次的位址、在區網裡自動尋找、找不到就退回預設。
 *
 * 為什麼要有這支
 * ------------
 * Fog 是「一個場域一台」，而 Pi 會在家裡、實驗室之間移動，每個網路分到的 IP
 * 都不同，寫死在程式裡的位址到了別的場域就連不上。手機 App 讀不到區網裡其他
 * 裝置的 MAC 位址（iOS、Android 都不開放），所以改用「逐一詢問同網段的
 * :3001/health，認得出是 Fog 的就用它」。
 *
 * 認得出的依據：Fog Node 層 /health 回的 `layer: "fog-node"`（Pi 上 09-17 的
 * 舊版就有），或新版加的 `service: "foodscan-fog"`。區網裡剛好也開 3001 埠的
 * 其他服務不會回這兩個欄位，不會被誤認。
 *
 * 限制：只掃手機所在的 /24（例如 192.168.1.1～254）。學校或公司網路常是更大
 * 的網段、或開了用戶端隔離（同一個 Wi-Fi 下的裝置彼此看不到），那種環境掃不到，
 * 要手動輸入位址或改用 Tailscale 位址。
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Network from 'expo-network';
import { FOG_URL } from '../constants/endpoints';

export const FOG_PORT = 3001;
/** 寫在 endpoints.ts 的預設位址（Tailscale），拿掉路徑只留 scheme://host:port。 */
export const DEFAULT_FOG_BASE = FOG_URL.replace(/\/query$/, '');
const STORAGE_KEY = 'fog.base';

let current = DEFAULT_FOG_BASE;

/** 送查詢用的完整網址。queryClient 每次送出時讀這裡，換位址不必重啟 App。 */
export function getFogQueryUrl(): string {
  return `${current}/query`;
}

export function getFogBase(): string {
  return current;
}

export async function setFogBase(base: string): Promise<void> {
  current = normalizeBase(base);
  try { await AsyncStorage.setItem(STORAGE_KEY, current); } catch { /* 存不進去下次再找一次就好 */ }
}

export async function loadSavedFogBase(): Promise<string | null> {
  try {
    const v = await AsyncStorage.getItem(STORAGE_KEY);
    if (v) current = v;
    return v;
  } catch {
    return null;
  }
}

/** 使用者手動輸入時常漏 http:// 或多打 /query、結尾斜線，一律整理成 http://host:port。 */
export function normalizeBase(input: string): string {
  let s = input.trim().replace(/\/+$/, '').replace(/\/query$/, '');
  if (!/^https?:\/\//.test(s)) s = `http://${s}`;
  if (!/:\d+$/.test(s.replace(/^https?:\/\//, ''))) s = `${s}:${FOG_PORT}`;
  return s;
}

export interface FogInfo {
  base: string;
  /** Fog 的名稱（新版 /health 才有，通常是 Pi 的主機名）。 */
  name: string | null;
  commit: string | null;
}

/** 問一台主機是不是 Fog。逾時或回應不像 Fog 都回 null，不丟例外。 */
export async function probeFog(base: string, timeoutMs = 800, fetchImpl: typeof fetch = fetch): Promise<FogInfo | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetchImpl(`${base}/health`, { signal: ctrl.signal });
    if (!r.ok) return null;
    const j = await r.json();
    if (j?.layer !== 'fog-node' && j?.service !== 'foodscan-fog') return null;
    return { base, name: j?.name ?? null, commit: j?.commit ?? null };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** 私有 IPv4 才掃（10.x、172.16–31.x、192.168.x）。行動網路、Tailscale 的 100.x 不掃。 */
export function lanCandidates(ip: string): string[] {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(ip.trim());
  if (!m) return [];
  const [a, b, c, d] = m.slice(1).map(Number);
  const priv = a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
  if (!priv) return [];
  const out: string[] = [];
  for (let h = 1; h <= 254; h++) if (h !== d) out.push(`http://${a}.${b}.${c}.${h}:${FOG_PORT}`);
  return out;
}

export interface ScanOptions {
  timeoutMs?: number;
  concurrency?: number;
  fetchImpl?: typeof fetch;
  onProgress?: (done: number, total: number) => void;
}

/** 同時問 concurrency 台，第一台認得出來的就回傳（其餘已送出的讓它們自己逾時）。 */
export async function scanForFog(candidates: string[], opts: ScanOptions = {}): Promise<FogInfo | null> {
  const { timeoutMs = 800, concurrency = 32, fetchImpl = fetch, onProgress } = opts;
  let next = 0;
  let done = 0;
  let found: FogInfo | null = null;
  const worker = async () => {
    while (!found && next < candidates.length) {
      const base = candidates[next++];
      const info = await probeFog(base, timeoutMs, fetchImpl);
      done += 1;
      onProgress?.(done, candidates.length);
      if (info && !found) found = info;
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, candidates.length) }, worker));
  return found;
}

export type FogSource = 'saved' | 'lan' | 'default' | 'none';

export interface ResolveResult {
  info: FogInfo | null;
  source: FogSource;
}

export interface ResolveDeps {
  getIp?: () => Promise<string>;
  fetchImpl?: typeof fetch;
  onProgress?: (done: number, total: number) => void;
}

/**
 * 找 Fog 的順序：上次用的位址 → 同網段掃描 → 預設（Tailscale）位址。
 * 找到就記起來，下次開 App 第一個就問它，通常一次就中、不必掃。
 * 都找不到時不改動目前的位址——使用者可能正要手動輸入。
 */
export async function resolveFog(deps: ResolveDeps = {}): Promise<ResolveResult> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const getIp = deps.getIp ?? Network.getIpAddressAsync;

  const saved = current;
  const s = await probeFog(saved, 1500, fetchImpl);
  if (s) return { info: s, source: 'saved' };

  let ip = '';
  try { ip = await getIp(); } catch { /* 拿不到 IP 就跳過掃描 */ }
  const cands = lanCandidates(ip);
  if (cands.length) {
    const f = await scanForFog(cands, { fetchImpl, onProgress: deps.onProgress });
    if (f) {
      await setFogBase(f.base);
      return { info: f, source: 'lan' };
    }
  }

  if (saved !== DEFAULT_FOG_BASE) {
    const d = await probeFog(DEFAULT_FOG_BASE, 3000, fetchImpl);
    if (d) {
      await setFogBase(d.base);
      return { info: d, source: 'default' };
    }
  }
  return { info: null, source: 'none' };
}
