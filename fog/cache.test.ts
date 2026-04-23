import { initDB, getCache, setCache, deleteCache, getCacheStats } from './cache';
import Database from 'better-sqlite3';

describe('Fog SQLite Cache Module', () => {
  beforeAll(() => {
    initDB();
  });

  afterEach(() => {
    // 每個測試完清空測試資料
    const db = new Database('fog_cache.db');
    db.prepare('DELETE FROM cache').run();
  });

  test('Should set and get cache correctly', () => {
    const mockBarcode = '4710000000001';
    const mockData = { name: 'Test Product', score: 100 };
    
    setCache(mockBarcode, mockData, 60);
    const result = getCache(mockBarcode);
    
    expect(result).toEqual(mockData);
  });

  test('Should return null for expired cache', () => {
    const mockBarcode = '4710000000002';
    const mockData = { name: 'Expired Product' };
    
    // 設定已過期的 timestamp (TTL=1, 2秒前更新)
    setCache(mockBarcode, mockData, -10); 
    
    const result = getCache(mockBarcode);
    expect(result).toBeNull();
  });

  test('Should report correct stats', () => {
    setCache('1', { a: 1 }, 3600); // 正常
    setCache('2', { b: 2 }, -10);  // 已過期
    
    const stats = getCacheStats();
    expect(stats.total).toBe(2);
    expect(stats.expired).toBe(1);
  });
});
