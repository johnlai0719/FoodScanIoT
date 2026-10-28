import { makeEntry, upsert, relativeTime, MAX_HISTORY } from '../scanHistory';
import { AnalysisResponse } from '../../types';

const base = (over: Partial<AnalysisResponse> & { barcode?: string } = {}) =>
  ({
    health_score: 12,
    risk_level: 'medium',
    nutri_grade: 'C',
    score_breakdown: [],
    allergen_warnings: [],
    food_safety_events: [],
    ingredients_detail: [],
    product_info: { name: '測試飲料', brand: '測試品牌' },
    ...over,
  } as unknown as AnalysisResponse);

describe('makeEntry', () => {
  it('取出清單要用的欄位，並整份結果一起留著', () => {
    const e = makeEntry(base({ barcode: '4710018123456' } as any))!;
    expect(e.id).toBe('4710018123456');
    expect(e.name).toBe('測試飲料');
    expect(e.healthScore).toBe(12);
    expect(e.nutriGrade).toBe('C');
    // 整份結果要在，點回去才能離線重現同一個結果頁
    expect(e.result.ingredients_detail).toBeDefined();
  });

  it('沒有條碼時用品名當 id——純拍照的查詢沒有穩定識別碼', () => {
    expect(makeEntry(base())!.id).toBe('測試飲料');
  });

  it('沒有 health_score 的一律不存', () => {
    // rejected / not_found / degraded 都是這個形狀，結果頁渲染不了
    expect(makeEntry({ status: 'rejected', message: '請重拍' } as any)).toBeNull();
    expect(makeEntry(null)).toBeNull();
  });

  it('0 分是合法分數，要存', () => {
    // 0 是真的分數，不是「沒有分數」——用 !health_score 判斷就會漏掉它
    expect(makeEntry(base({ health_score: 0 }))).not.toBeNull();
  });

  it('品名是空字串時給一個可顯示的替代，不讓清單出現空白列', () => {
    const e = makeEntry(base({ product_info: { name: '  ' } } as any))!;
    expect(e.name).toBe('未命名產品');
  });
});

describe('upsert', () => {
  const mk = (id: string) => ({ ...makeEntry(base())!, id, name: id });

  it('最新的排最前面', () => {
    const list = upsert(upsert([], mk('A')), mk('B'));
    expect(list.map(e => e.id)).toEqual(['B', 'A']);
  });

  it('同一項重掃只留一筆，並移到最前面', () => {
    let list = upsert(upsert(upsert([], mk('A')), mk('B')), mk('C'));
    list = upsert(list, mk('A'));
    expect(list.map(e => e.id)).toEqual(['A', 'C', 'B']);
    expect(list.filter(e => e.id === 'A')).toHaveLength(1);
  });

  it('超過上限時擠掉最舊的', () => {
    let list: ReturnType<typeof mk>[] = [];
    for (const id of ['A', 'B', 'C', 'D', 'E', 'F']) list = upsert(list, mk(id));
    expect(list).toHaveLength(MAX_HISTORY);
    expect(list.map(e => e.id)).toEqual(['F', 'E', 'D', 'C', 'B']);
    expect(list.find(e => e.id === 'A')).toBeUndefined();
  });

  it('不改動傳進來的陣列', () => {
    const original = [mk('A')];
    upsert(original, mk('B'));
    expect(original).toHaveLength(1);
  });
});

describe('relativeTime', () => {
  const now = new Date('2026-09-22T12:00:00Z');
  it.each([
    ['2026-09-22T11:59:40Z', '剛剛'],
    ['2026-09-22T11:30:00Z', '30 分鐘前'],
    ['2026-09-22T09:00:00Z', '3 小時前'],
    ['2026-09-20T12:00:00Z', '2 天前'],
  ])('%s → %s', (iso, expected) => {
    expect(relativeTime(iso, now)).toBe(expected);
  });

  it('超過一週給日期——「37 天前」沒人在心算', () => {
    expect(relativeTime('2026-08-15T12:00:00Z', now)).toBe('8/15');
  });

  it('壞掉的時間字串回空字串，不顯示 NaN', () => {
    expect(relativeTime('not-a-date', now)).toBe('');
  });
});
