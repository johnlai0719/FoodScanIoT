import Database from 'better-sqlite3';
import path from 'path';

const db = new Database('fog_cache.db');

export interface CacheResult {
  barcode: string;
  result_json: string;
  last_updated: number;
  ttl: number;
}

/**
 * 初始化資料庫
 */
export function initDB() {
  db.exec(`
    CREATE TABLE IF NOT EXISTS cache (
      barcode TEXT PRIMARY KEY,
      result_json TEXT NOT NULL,
      last_updated INTEGER NOT NULL,
      ttl INTEGER NOT NULL
    )
  `);
  
  db.exec(`
    CREATE TABLE IF NOT EXISTS feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      barcode TEXT,
      original_result TEXT,
      user_correction TEXT,
      synced INTEGER DEFAULT 0,
      created_at TEXT
    )
  `);
  console.log('📦 SQLite Cache Database Initialized (including feedback table).');
}

/**
 * 儲存使用者反饋 (Task B)
 */
export function saveFeedback(barcode: string, originalResult: any, userCorrection: any) {
  const statement = db.prepare(`
    INSERT INTO feedback (barcode, original_result, user_correction, created_at)
    VALUES (?, ?, ?, ?)
  `);
  
  statement.run(
    barcode,
    JSON.stringify(originalResult),
    JSON.stringify(userCorrection),
    new Date().toISOString()
  );
  console.log(`[Feedback] Saved for barcode: ${barcode}`);
}

/**
 * 取得尚未同步的反饋 (Task B)
 */
export function getPendingFeedback(): any[] {
  return db.prepare('SELECT * FROM feedback WHERE synced = 0').all();
}

/**
 * 標記反饋為已同步 (Task B)
 */
export function markFeedbackSynced(id: number) {
  db.prepare('UPDATE feedback SET synced = 1 WHERE id = ?').run(id);
  console.log(`[Feedback] Marked as synced: ID ${id}`);
}

/**
 * 取得快取資料
 */
export function getCache(barcode: string): any | null {
  const row = db.prepare('SELECT * FROM cache WHERE barcode = ?').get(barcode) as CacheResult;
  
  if (!row) return null;

  const now = Math.floor(Date.now() / 1000);
  const isExpired = now > row.last_updated + row.ttl;

  if (isExpired) {
    console.log(`[Cache] Expired for barcode: ${barcode}`);
    return null;
  }

  return JSON.parse(row.result_json);
}

/**
 * 寫入快取
 * @param barcode 
 * @param result 結果物件
 * @param ttl 過期時間 (秒)，預設 3600
 */
export function setCache(barcode: string, result: object, ttl: number = 3600) {
  const statement = db.prepare(`
    INSERT OR REPLACE INTO cache (barcode, result_json, last_updated, ttl)
    VALUES (?, ?, ?, ?)
  `);
  
  statement.run(
    barcode,
    JSON.stringify(result),
    Math.floor(Date.now() / 1000),
    ttl
  );
  console.log(`[Cache] Saved for barcode: ${barcode}`);
}

/**
 * 刪除特定快取
 */
export function deleteCache(barcode: string) {
  db.prepare('DELETE FROM cache WHERE barcode = ?').run(barcode);
}

/**
 * 統計快取狀態
 */
export function getCacheStats() {
  const total = (db.prepare('SELECT count(*) as count FROM cache').get() as any).count;
  const now = Math.floor(Date.now() / 1000);
  const expired = (db.prepare('SELECT count(*) as count FROM cache WHERE ? > last_updated + ttl').get(now) as any).count;
  
  return { total, expired };
}

/**
 * 取得「舊」快取 (用於降階回應)
 */
export function getStaleCache(barcode: string): any | null {
  const row = db.prepare('SELECT * FROM cache WHERE barcode = ?').get(barcode) as CacheResult;
  return row ? JSON.parse(row.result_json) : null;
}
