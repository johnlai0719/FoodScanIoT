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
  // 2026-09-13 重寫。**舊版是編的**：寫死 55/35/10、依條碼給魔術數字
  // （TEST → 68/22/10）、缺值時 `sugarPoints || 6` 捏一個 6 出來，還有一個
  // 從來沒算過的「其他膳食營養 10%」。而且只看糖與熱量，飽和脂肪、鈉、
  // 蛋白質、纖維全部忽略——圓環看起來像在表達什麼，其實沒有。
  //
  // 現在每一項都帶真的 points，占比就是 |這項| ÷ Σ|全部扣分|。
  // 只畫扣分：Nutri-Score = 扣分 − 加分，把相減塞進同一個圓餅會誤導
  // （一塊「負的面積」不存在）。加分另外由 bonusSegments 畫成內圈。
  const penalties = scoreBreakdownList.filter(i => i.points < 0);
  const total = penalties.reduce((n, i) => n + Math.abs(i.points), 0);
  if (total === 0) return [];

  return penalties
    .map(item => {
      const abs = Math.abs(item.points);
      return {
        name: item.reason,
        percentage: (abs / total) * 100,
        color: PENALTY_COLORS[item.key ?? ''] ?? '#9ca3af',
        // 同時給「幾分」與「佔幾成」：只有百分比看不出量級，
        // 只有分數看不出這一項在總扣分裡有多重。
        valueText: `${abs} 分・${Math.round((abs / total) * 100)}%`,
      };
    })
    // 0 分的項目不畫（畫出來是寬度 0 的弧），但它們仍留在明細清單裡。
    .filter(seg => seg.percentage > 0);
}

/**
 * 加分項。畫成內圈，長度是「加分 ÷ 扣分」——它抵銷了多少。
 * 沒有加分時回空陣列，內圈整個不畫（而不是畫一個 0 長度的弧）。
 */
export function getBonusSegments(
  scoreBreakdownList: ScoreBreakdownItem[]
): HealthSegment[] {
  const bonuses = scoreBreakdownList.filter(i => i.points > 0);
  const penaltyTotal = scoreBreakdownList
    .filter(i => i.points < 0)
    .reduce((n, i) => n + Math.abs(i.points), 0);
  if (bonuses.length === 0 || penaltyTotal === 0) return [];

  return bonuses.map(item => ({
    name: item.reason,
    // 以扣分總額為分母：內圈畫滿就代表加分完全抵銷了扣分。
    percentage: Math.min((item.points / penaltyTotal) * 100, 100),
    color: BONUS_COLORS[item.key ?? ''] ?? '#10b981',
    valueText: `+${item.points} 分`,
  }));
}

// 每一項固定一個顏色，換產品時同一項才會是同一色（隨機或依序上色的話，
// 兩個產品的圓環無法對照）。色相取自 Nutri-Score 官方色階的紅橙端。
const PENALTY_COLORS: Record<string, string> = {
  sugars: '#e63e11',      // 糖：最重的那一項，用最深的紅
  energy: '#ee8100',      // 熱量
  sfa: '#b45309',         // 飽和脂肪
  salt: '#7c2d12',        // 鈉
  sweeteners: '#a16207',  // 非營養性甜味劑
};

const BONUS_COLORS: Record<string, string> = {
  protein: '#038141',
  fibre: '#85bb2f',
  fruit_veg: '#4d7c0f',
};

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
