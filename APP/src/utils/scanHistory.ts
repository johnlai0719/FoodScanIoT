import AsyncStorage from '@react-native-async-storage/async-storage';
import { AnalysisResponse } from '../types';

/**
 * 本機查詢歷史（最近 5 筆）。
 *
 * 存的是**整份分析結果**而不是條碼，所以點回去能離線重現同一個結果頁，
 * 不必重打一次分析——重打不只是等 10 秒，而是同一張照片重跑辨識未必得到
 * 一樣的結果（見讀取器的執行間變異），那樣「歷史紀錄」就名不副實了。
 *
 * 大小：實測一份完整回應約 68 KB（ingredients_detail 佔 22 KB），五筆約
 * 340 KB。AsyncStorage 放得下，但這也是為什麼上限是個位數而不是「全部留著」。
 */

export const HISTORY_KEY = 'scanHistory';
export const MAX_HISTORY = 5;

export interface HistoryEntry {
  /** 去重用。有條碼用條碼，沒有就用品名——純拍照的查詢沒有穩定識別碼。 */
  id: string;
  /** 存入時間（ISO 8601）。不是 Cloud 的 processed_at：快取命中時那個是舊的，
   *  而這裡要答的是「我什麼時候查的」。 */
  savedAt: string;
  name: string;
  brand?: string;
  healthScore: number;
  nutriGrade?: string | null;
  riskLevel: string;
  result: AnalysisResponse;
}

/**
 * 把一份分析結果轉成歷史項目。
 *
 * 沒有 health_score 的一律回 null：那是失敗或降階的形狀
 * （rejected／not_found／degraded），結果頁渲染不了，存進去只會變成點了沒反應
 * 的項目。降階結果另有自己的畫面，不走這條。
 */
export function makeEntry(result: AnalysisResponse | null, now: Date = new Date()): HistoryEntry | null {
  if (!result || result.health_score === undefined || result.health_score === null) return null;
  const info = result.product_info ?? ({} as NonNullable<AnalysisResponse['product_info']>);
  const name = (info.name || '').trim() || '未命名產品';
  const barcode = (result as { barcode?: string }).barcode;
  return {
    id: (barcode && String(barcode).trim()) || name,
    savedAt: now.toISOString(),
    name,
    brand: info.brand,
    healthScore: result.health_score,
    nutriGrade: result.nutri_grade ?? null,
    riskLevel: result.risk_level,
    result,
  };
}

/**
 * 加入一筆，並維持「最新在前、同一項只留一筆、最多 max 筆」。
 *
 * 同 id 的舊紀錄會被移除而不是並存：重掃同一罐通常是想看新的結果，
 * 兩筆並排只會讓五格的空間被同一個產品佔掉。
 */
export function upsert(
  list: HistoryEntry[],
  entry: HistoryEntry,
  max: number = MAX_HISTORY,
): HistoryEntry[] {
  const rest = list.filter(e => e.id !== entry.id);
  return [entry, ...rest].slice(0, max);
}

/**
 * 讀取歷史。
 *
 * 解析失敗時回空陣列而不是拋例外：這是輔助功能，格式變動或儲存損毀都不該
 * 讓掃描主流程掛掉。同理，讀到的不是陣列也當作沒有。
 */
export async function loadHistory(): Promise<HistoryEntry[]> {
  try {
    const raw = await AsyncStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as HistoryEntry[]) : [];
  } catch {
    return [];
  }
}

export async function saveHistory(list: HistoryEntry[]): Promise<void> {
  try {
    await AsyncStorage.setItem(HISTORY_KEY, JSON.stringify(list));
  } catch {
    // 寫不進去就算了——歷史紀錄不值得打斷使用者正在看的結果。
  }
}

/** 記錄一次查詢，回傳更新後的清單。不符合存入條件時原樣回傳。 */
export async function recordScan(result: AnalysisResponse | null): Promise<HistoryEntry[]> {
  const entry = makeEntry(result);
  const current = await loadHistory();
  if (!entry) return current;
  const next = upsert(current, entry);
  await saveHistory(next);
  return next;
}

export async function clearHistory(): Promise<void> {
  try {
    await AsyncStorage.removeItem(HISTORY_KEY);
  } catch {
    // 同 saveHistory
  }
}

/** 相對時間。只到「天」為止——更久的直接給日期，「37 天前」沒人在心算。 */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return '';
  const mins = Math.floor((now.getTime() - then.getTime()) / 60000);
  if (mins < 1) return '剛剛';
  if (mins < 60) return `${mins} 分鐘前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小時前`;
  const days = Math.floor(hours / 24);
  if (days <= 7) return `${days} 天前`;
  return `${then.getMonth() + 1}/${then.getDate()}`;
}
