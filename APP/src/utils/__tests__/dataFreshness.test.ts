import {
  formatAbsoluteTime,
  formatRelativeTime,
  getDataFreshness,
} from '../dataFreshness';

const NOW = new Date('2026-08-05T12:00:00');
const ago = (seconds: number) => new Date(NOW.getTime() - seconds * 1000).toISOString();

describe('formatRelativeTime', () => {
  it.each([
    [30, '剛剛'],
    [60, '1 分鐘前'],
    [59 * 60, '59 分鐘前'],
    [60 * 60, '1 小時前'],
    [23 * 3600, '23 小時前'],
    [24 * 3600, '1 天前'],
    [29 * 24 * 3600, '29 天前'],
  ])('%i 秒前 -> %s', (seconds, expected) => {
    expect(formatRelativeTime(ago(seconds), NOW)).toBe(expected);
  });

  it('超過 30 天回傳 null（交給絕對時間呈現）', () => {
    expect(formatRelativeTime(ago(31 * 24 * 3600), NOW)).toBeNull();
  });

  it('容許 60 秒內的時鐘誤差，再往前就回 null', () => {
    expect(formatRelativeTime(new Date(NOW.getTime() + 30_000).toISOString(), NOW)).toBe('剛剛');
    expect(formatRelativeTime(new Date(NOW.getTime() + 600_000).toISOString(), NOW)).toBeNull();
  });

  it('無效或缺席的輸入回傳 null，不拋錯', () => {
    expect(formatRelativeTime(undefined, NOW)).toBeNull();
    expect(formatRelativeTime('', NOW)).toBeNull();
    expect(formatRelativeTime('not-a-date', NOW)).toBeNull();
  });
});

describe('formatAbsoluteTime', () => {
  it('補零並以本地時間格式輸出', () => {
    expect(formatAbsoluteTime('2026-03-07T09:05:00')).toBe('2026/03/07 09:05');
  });

  it('無效輸入回傳 null', () => {
    expect(formatAbsoluteTime('not-a-date')).toBeNull();
    expect(formatAbsoluteTime(undefined)).toBeNull();
  });
});

describe('getDataFreshness', () => {
  it('剛算好的結果', () => {
    const f = getDataFreshness({ processed_at: ago(10), cached: false }, NOW);
    expect(f.label).toBe('更新於 剛剛');
    expect(f.isStale).toBe(false);
    expect(f.warning).toBeUndefined();
  });

  it('快取命中時顯示的是原始計算時間，不是當下', () => {
    const f = getDataFreshness({ processed_at: ago(45 * 60), cached: true }, NOW);
    expect(f.label).toBe('更新於 45 分鐘前');
    expect(f.isStale).toBe(false);
  });

  it('超過 24 小時標記為過舊', () => {
    expect(getDataFreshness({ processed_at: ago(25 * 3600) }, NOW).isStale).toBe(true);
    expect(getDataFreshness({ processed_at: ago(23 * 3600) }, NOW).isStale).toBe(false);
  });

  it('Fog 降級回應一律視為過舊，並帶出警告文字', () => {
    const f = getDataFreshness(
      { processed_at: ago(60), cached: true, _warning: '⚠️ 目前無法取得最新資料，為您顯示舊有快取' },
      NOW,
    );
    expect(f.isStale).toBe(true);
    expect(f.warning).toContain('無法取得最新資料');
  });

  // 缺 processed_at 時不可假裝資料是新的——那正是原本寫死「資料校準時間：2026」的問題
  it('缺少時間資訊時誠實顯示「不明」，不編造', () => {
    const f = getDataFreshness({ cached: true }, NOW);
    expect(f.label).toBe('更新時間不明');
    expect(f.isStale).toBe(true);
  });

  it('result 為 null 不崩潰', () => {
    expect(getDataFreshness(null, NOW).label).toBe('更新時間不明');
  });

  it('超過 30 天改以絕對時間呈現', () => {
    const f = getDataFreshness({ processed_at: '2026-03-07T09:05:00' }, NOW);
    expect(f.label).toBe('更新於 2026/03/07 09:05');
    expect(f.isStale).toBe(true);
  });
});
