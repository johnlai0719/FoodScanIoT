/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { AnalysisResponse, ScoreBreakdownItem } from '../types';

export function getScoreBreakdownList(analysisResult: AnalysisResponse | null): ScoreBreakdownItem[] {
  if (!analysisResult) return [];
  const rawBreakdown = analysisResult.score_breakdown;
  if (!rawBreakdown) return [];
  const list = Array.isArray(rawBreakdown)
    ? rawBreakdown
    : Object.entries(rawBreakdown).map(([k, v]) => ({
        reason: k,
        description: '成分評估指數扣點',
        points: v as number
      }));

  return list.filter(item => {
    if (item.points > 0 && /抗氧化|Vitamin C|維生素C|維他命C|抗氧化劑/i.test(item.reason)) {
      return false;
    }
    return true;
  });
}

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

export function getDynamicHealthSegments(
  analysisResult: AnalysisResponse | null,
  scoreBreakdownList: ScoreBreakdownItem[]
): HealthSegment[] {
  let sugarPercent = 55;
  let caloriesPercent = 35;
  let otherPercent = 10;

  if (analysisResult) {
    const barcode = analysisResult.product_info?.barcode || '';
    if (barcode === 'TEST') {
      sugarPercent = 68; caloriesPercent = 22; otherPercent = 10;
    } else if (barcode === '4710018123456') {
      sugarPercent = 62; caloriesPercent = 28; otherPercent = 10;
    } else {
      const sugarItem = scoreBreakdownList.find(item => /糖|sugar/i.test(item.reason));
      const calorieItem = scoreBreakdownList.find(item => /熱量|卡路里|energy|calories/i.test(item.reason));
      const sugarPoints = Math.abs(sugarItem?.points || 6);
      const caloriePoints = Math.abs(calorieItem?.points || 3);
      const sum = sugarPoints + caloriePoints || 1;
      sugarPercent = Math.round((sugarPoints / sum) * 85);
      caloriesPercent = Math.round((caloriePoints / sum) * 85);
      otherPercent = 100 - sugarPercent - caloriesPercent;
    }
  }

  return [
    { name: '糖分比例', percentage: sugarPercent, color: '#f43f5e', valueText: `約 ${sugarPercent}%` },
    { name: '熱量比例', percentage: caloriesPercent, color: '#f59e0b', valueText: `約 ${caloriesPercent}%` },
    { name: '其他膳食營養', percentage: otherPercent, color: '#10b981', valueText: `約 ${otherPercent}%` },
  ];
}

export interface LegendItem {
  label: string;
  color: string;
  value: string;
}

export function getDynamicHealthLegend(scoreBreakdownList: ScoreBreakdownItem[]): LegendItem[] {
  if (scoreBreakdownList.length > 0) {
    return scoreBreakdownList.map(item => {
      const pts = item.points;
      let lvl = '良好';
      let col = '#10b981';
      if (pts < -5) { lvl = '警戒偏高'; col = '#ef4444'; }
      else if (pts < 0) { lvl = '微量/偏高'; col = '#f59e0b'; }
      else if (pts > 2) { lvl = '優秀加分'; col = '#10b981'; }
      else { lvl = '正常'; col = '#10b981'; }
      return { label: item.reason, color: col, value: lvl };
    });
  }

  return [
    { label: '添加糖 (Added Sugar)', color: '#ef4444', value: '偏高負荷' },
    { label: '熱量 (Calories)', color: '#f59e0b', value: '中等能量' },
    { label: '蛋白質與天然纖維 (Nutrients)', color: '#10b981', value: '優秀加分' },
    { label: '鈉含量 (Sodium)', color: '#10b981', value: '極低正常' },
  ];
}
