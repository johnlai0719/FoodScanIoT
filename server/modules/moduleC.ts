import { pool } from '../server';

export interface FoodSafetyResult {
  food_safety_level: 'low' | 'medium' | 'high' | 'critical';
  events: any[];
  summary_text: string;
}

/**
 * 模組 C：食安風險引擎
 */
export async function processModuleC(
  barcode: string,
  manufacturer: string
): Promise<FoodSafetyResult> {
  
  // 1. 查詢資料表 (barcode 或製造商)
  const [eventRows] = await pool.query(`
    SELECT * FROM food_safety_events 
    WHERE barcode = ? OR manufacturer = ?
    ORDER BY event_date DESC
  `, [barcode, manufacturer]);

  const events = eventRows as any[];
  let safetyLevel: 'low' | 'medium' | 'high' | 'critical' = 'low';

  const now = new Date();
  const sixMonthsAgo = new Date(now.setMonth(now.getMonth() - 6));
  const oneYearAgo = new Date(now.setFullYear(now.getFullYear() - 1));
  const twoYearsAgo = new Date(now.setFullYear(now.getFullYear() - 2));

  // 2. 判斷等級邏輯
  for (const ev of events) {
    const evDate = new Date(ev.event_date);
    if (evDate >= sixMonthsAgo && ev.severity === 'critical') {
      safetyLevel = 'critical';
      break;
    }
    if (evDate >= oneYearAgo && ev.severity === 'high' && safetyLevel !== 'critical') {
      safetyLevel = 'high';
    }
    if (evDate >= twoYearsAgo && ev.severity === 'medium' && safetyLevel === 'low') {
      safetyLevel = 'medium';
    }
  }

  // 3. 規則化摘要
  let summary = "目前該產品/製造商查無食安違規紀錄，請安心食用。";
  if (events.length > 0) {
    const latest = events[0];
    summary = `該產品/製造商近期有 ${events.length} 筆食安事件，最嚴重等級為 ${latest.severity}（事件日期：${latest.event_date.toISOString().split('T')[0]}）。`;
  }

  return {
    food_safety_level: safetyLevel,
    events: events.slice(0, 5), // 回傳最近 5 筆
    summary_text: summary
  };
}
