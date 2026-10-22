import { AnalysisResponse } from '../../types';
import { formatNutritionValue, getAdditiveCategoryStats, getResultPresentation, getNutritionRingSegments, getPenaltyColor } from '../resultPresentation';

const makeResult = (overrides: Partial<AnalysisResponse> = {}): AnalysisResponse => ({
  health_score: 17, risk_level: 'medium', score_breakdown: [],
  allergen_warnings: [], food_safety_events: [], ingredients_detail: [], ...overrides,
});

describe('result presentation', () => {
  it('builds the ring and legend from actual penalties with shared colors and proportions', () => {
    const result = makeResult({ score_breakdown: [
      { reason: '熱量', description: '', points: -1, key: 'energy' },
      { reason: '鈉', description: '', points: -16, key: 'salt' },
      { reason: '糖', description: '', points: 0, key: 'sugars' },
      { reason: '蛋白質', description: '', points: 3, key: 'protein' },
    ] });
    const segments = getNutritionRingSegments(result);
    expect(segments.map(item => item.key)).toEqual(['salt', 'energy']);
    expect(segments[0].fraction).toBeCloseTo(16 / 17);
    expect(segments.reduce((sum, item) => sum + item.fraction, 0)).toBeCloseTo(1);
    expect(segments[0].color).toBe(getPenaltyColor('salt'));
    expect(segments[1].color).not.toBe(segments[0].color);
  });

  it('does not invent ring portions when no penalties were supplied', () => {
    expect(getNutritionRingSegments(makeResult({ nutri_grade: 'A' }))).toEqual([]);
    expect(getPenaltyColor('legacy')).toBe('#87929C');
    expect(getPenaltyColor()).toBe('#87929C');
  });

  it('counts every distinct official function, without counting unmatched or near-miss ingredients', () => {
    const stats = getAdditiveCategoryStats([
      { name: 'A', isAdditive: true, description: '', category: ['調味劑', '調味劑', ' 抗氧化劑 '] },
      { name: 'B', isAdditive: 'true', description: '', category: ['調味劑'] },
      { name: 'C', isAdditive: false, description: '', category: ['甜味劑'], nearMiss: { officialName: 'D', distance: 1, scannedLength: 8 } },
    ]);
    expect(stats.additiveCount).toBe(2);
    expect(stats.totalAssignments).toBe(3);
    expect(stats.rows).toEqual([{ name: '調味劑', count: 2 }, { name: '抗氧化劑', count: 1 }]);
  });

  it('keeps missing classifications in a separate last bucket', () => {
    const stats = getAdditiveCategoryStats([
      { name: 'A', isAdditive: true, description: '' },
      { name: 'B', isAdditive: true, description: '', category: [' ', ''] },
      { name: 'C', isAdditive: true, description: '', category: ['黏稠劑'] },
    ]);
    expect(stats.rows).toEqual([{ name: '黏稠劑', count: 1 }, { name: '未分類', count: 2 }]);
    expect(stats.totalAssignments).toBe(3);
    expect(getAdditiveCategoryStats([])).toEqual({ additiveCount: 0, rows: [], totalAssignments: 0 });
  });

  it('uses the server grade without deriving a grade from the raw score', () => {
    expect(getResultPresentation(makeResult()).grade).toBeUndefined();
    expect(getResultPresentation(makeResult({ nutri_grade: ' b ' })).grade).toBe('B');
    expect(getResultPresentation(makeResult({ nutri_grade: 'unknown' })).grade).toBeUndefined();
  });

  it('keeps missing values separate from a real zero', () => {
    expect(formatNutritionValue(null)).toBe('未標示');
    expect(formatNutritionValue(undefined)).toBe('未標示');
    expect(formatNutritionValue(NaN)).toBe('未標示');
    expect(formatNutritionValue(0)).toBe('0');
    expect(getResultPresentation(makeResult({ health_score: 0 })).score).toBe(0);
    expect(getResultPresentation(makeResult({ health_score: NaN })).score).toBeNull();
    expect(getResultPresentation(makeResult({ data: { nutrition_facts: { sodium: 0, sugar: null } } })).nutrition)
      .toEqual({ sodium: 0, sugar: null });
  });

  it('counts only confirmed additive matches, retaining near misses as unmatched', () => {
    const model = getResultPresentation(makeResult({ ingredients_detail: [
      { name: 'A', isAdditive: true, description: '' },
      { name: 'B', isAdditive: 'true', description: '' },
      { name: 'C', isAdditive: 'false', description: '', nearMiss: { officialName: 'D', distance: 1, scannedLength: 8 } },
      { name: 'E', isAdditive: false, description: '' },
    ] }));
    expect(model.additives.map(item => item.name)).toEqual(['A', 'B']);
    expect(model.unmatched.map(item => item.name)).toEqual(['C', 'E']);
    expect(model.ingredients).toHaveLength(4);
  });

  it('finds the strongest actual penalty without mutating the server breakdown', () => {
    const result = makeResult({ score_breakdown: [
      { reason: '熱量', description: '', points: -1, key: 'energy' },
      { reason: '蛋白質', description: '', points: 5, key: 'protein' },
      { reason: '鈉', description: '', points: -16, key: 'salt' },
    ] });
    const model = getResultPresentation(result);
    expect(model.leadingPenalty?.key).toBe('salt');
    expect(model.penalties.map(item => item.points)).toEqual([-16, -1]);
    expect((result.score_breakdown as { points: number }[]).map(item => item.points)).toEqual([-1, 5, -16]);
    expect(getResultPresentation(makeResult()).leadingPenalty).toBeUndefined();
  });
});
