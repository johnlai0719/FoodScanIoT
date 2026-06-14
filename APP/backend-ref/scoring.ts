// FoodScan — 前端如何解析 score_breakdown 的邏輯
// 後端參考此檔，了解 health_score 和 score_breakdown 如何被前端使用

import { AnalysisResponse, ScoreBreakdownItem } from './types';

/**
 * 將 score_breakdown 標準化為陣列格式。
 * 前端同時接受兩種後端格式：
 *   1. 陣列：[{ reason, description, points }]  ← 建議使用
 *   2. 物件：{ "高含糖量": -8, "茶多酚": 3 }    ← 舊格式相容
 */
export function getScoreBreakdownList(analysisResult: AnalysisResponse | null): ScoreBreakdownItem[] {
  if (!analysisResult) return [];
  const rawBreakdown = analysisResult.score_breakdown;
  const list = Array.isArray(rawBreakdown)
    ? rawBreakdown
    : Object.entries(rawBreakdown).map(([k, v]) => ({
        reason: k,
        description: '成分評估指數扣點',
        points: v as number
      }));

  // 抗氧化劑加分項目不顯示在扣分表中
  return list.filter(item => {
    if (item.points > 0 && /抗氧化|Vitamin C|維生素C|維他命C|抗氧化劑/i.test(item.reason)) {
      return false;
    }
    return true;
  });
}

/**
 * 從 score_breakdown 提取正分和負分總量，用於儀表板長條圖比例。
 * 若 breakdown 沒有資料，則從 health_score 推算。
 */
export function getDynamicHealthPoints(
  analysisResult: AnalysisResponse | null,
  scoreBreakdownList: ScoreBreakdownItem[]
): { pos: number; neg: number } {
  if (!analysisResult) return { pos: 3, neg: 8 };

  let posPoints = scoreBreakdownList
    .filter(item => item.points > 0)
    .reduce((sum, item) => sum + item.points, 0);

  let negPoints = Math.abs(
    scoreBreakdownList
      .filter(item => item.points < 0)
      .reduce((sum, item) => sum + item.points, 0)
  );

  if (posPoints === 0 && negPoints === 0) {
    const score = analysisResult.health_score ?? 100;
    if (score >= 80) {
      posPoints = Math.max(1, Math.round(score / 20));
      negPoints = Math.max(1, Math.round((100 - score) / 10));
    } else {
      posPoints = 3;
      negPoints = Math.max(1, Math.round((100 - score) / 10));
    }
  } else {
    if (posPoints === 0) posPoints = 3;
  }

  return { pos: posPoints, neg: negPoints };
}

export interface HealthSegment {
  name: string;
  percentage: number;
  color: string;
  valueText: string;
}

/**
 * 從 score_breakdown 推算糖分/熱量/其他的比例，用於圓形儀表板分段顯示。
 * reason 欄位含「糖/sugar」或「熱量/calories」的項目會被對應到對應分段。
 */
export function getDynamicHealthSegments(
  analysisResult: AnalysisResponse | null,
  scoreBreakdownList: ScoreBreakdownItem[]
): HealthSegment[] {
  let sugarPercent = 55;
  let caloriesPercent = 35;
  let otherPercent = 10;

  if (analysisResult) {
    const sugarItem = scoreBreakdownList.find(item => /糖|sugar/i.test(item.reason));
    const calorieItem = scoreBreakdownList.find(item => /熱量|卡路里|energy|calories/i.test(item.reason));
    const sugarPoints = Math.abs(sugarItem?.points || 6);
    const caloriePoints = Math.abs(calorieItem?.points || 3);
    const sum = sugarPoints + caloriePoints || 1;
    sugarPercent = Math.round((sugarPoints / sum) * 85);
    caloriesPercent = Math.round((caloriePoints / sum) * 85);
    otherPercent = 100 - sugarPercent - caloriesPercent;
  }

  return [
    { name: '糖分比例', percentage: sugarPercent, color: '#f43f5e', valueText: `約 ${sugarPercent}%` },
    { name: '熱量比例', percentage: caloriesPercent, color: '#f59e0b', valueText: `約 ${caloriesPercent}%` },
    { name: '其他膳食營養', percentage: otherPercent, color: '#10b981', valueText: `約 ${otherPercent}%` },
  ];
}
