import { AnalysisResponse, IngredientDetail } from '../types';
import { getScoreBreakdownList } from './scoring';

export const isMatchedAdditive = (item: IngredientDetail) =>
  item.isAdditive === true || item.isAdditive === 'true';

export const GRADE_STYLES: Record<string, { color: string; background: string; label: string }> = {
  A: { color: '#16834C', background: '#E7F6EC', label: '營養評級較佳' },
  B: { color: '#5D861F', background: '#F0F6DF', label: '營養評級良好' },
  C: { color: '#9A6A00', background: '#FFF5D9', label: '營養負擔適中' },
  D: { color: '#A43B10', background: '#FFF0CE', label: '營養負擔偏高' },
  E: { color: '#BE3030', background: '#FFF0F0', label: '營養負擔較高' },
};

export function getResultPresentation(result: AnalysisResponse) {
  const grade = result.nutri_grade?.trim().toUpperCase();
  const gradeStyle = (grade && GRADE_STYLES[grade]) || {
    color: '#68737B', background: '#F1F3F4', label: '尚無營養分級',
  };
  const breakdown = getScoreBreakdownList(result);
  const penalties = breakdown.filter(item => Number.isFinite(item.points) && item.points < 0)
    .sort((a, b) => a.points - b.points);
  const ingredients = result.ingredients_detail ?? [];
  const additives = ingredients.filter(isMatchedAdditive);
  return {
    grade: grade && GRADE_STYLES[grade] ? grade : undefined,
    gradeStyle,
    score: typeof result.health_score === 'number' && Number.isFinite(result.health_score)
      ? result.health_score : null,
    breakdown,
    penalties,
    leadingPenalty: penalties[0],
    ingredients,
    additives,
    unmatched: ingredients.filter(item => !isMatchedAdditive(item)),
    nutrition: result.data?.nutrition_facts ?? {},
  };
}

export function formatNutritionValue(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 }).format(value)
    : '未標示';
}

export const PENALTY_COLORS: Record<string, string> = {
  energy: '#FFA916', sugars: '#DB6515', sfa: '#CD511B', salt: '#A7380E',
  sweeteners: '#CB3F36',
};
