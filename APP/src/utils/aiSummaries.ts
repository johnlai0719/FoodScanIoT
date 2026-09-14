/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * 總結的**備援**文字。
 *
 * ⚠ 2026-09-14 重寫。原本這個檔案的每一支都寫死了兩個條碼的假文案，內容包括
 * 「檢出去水醋酸，法規禁止加在茶葉飲料」「該企業基本保持常規合格綠燈」
 * 「統一企業曾於 2024 年有老壇酸菜異物輿論」等等。那些是做畫面時編的示範文字，
 * 但它們**會真的顯示給使用者**——只要 Cloud 沒回傳總結就會走到這裡。
 * 對一個食安 App 而言，那是憑空產生的違規指控與安心背書，兩個方向都不可接受，
 * 其中針對具名企業的部分更有實際的法律風險。全數移除。
 *
 * 現在 Cloud 一律以規則產生總結（server/module_d/summary.py），正常情況下
 * 不會用到這裡。這些函式只在下列情形頂上：
 *   - 舊快取裡的結果沒有這兩個欄位
 *   - 降階回應（本機 OCR，刻意不含評分）
 *
 * 因此它們**只陳述手上有的計數**，不做任何判定、不猜測、不給安心話。
 */

import { AnalysisResponse, IngredientDetail } from '../types';

export function getAiProductSummary(
  analysisResult: AnalysisResponse | null,
  allIngredients: IngredientDetail[],
  totalAdditivesCount: number
): string {
  if (!analysisResult) return '';
  // 分數可能不存在（降階回應刻意不含 health_score）。沒有就不要提分數——
  // 寫「0 分」或「未知風險」都是把缺資料呈現成一種判定。
  const hasScore = typeof analysisResult.health_score === 'number';
  const head = hasScore
    ? `Nutri-Score 原始分數 ${analysisResult.health_score} 分（分數越低越健康）。`
    : '此筆結果未包含健康評分。';
  return `${head}辨識到 ${allIngredients.length} 項成分，其中 ${totalAdditivesCount} 項比對到食品添加物資料庫。詳細的逐項計分請點開評分明細。`;
}

export function getAiAdditivesSummary(
  analysisResult: AnalysisResponse | null,
  totalAdditivesCount: number,
  highRiskCount: number
): string {
  if (!analysisResult) return '';
  if (totalAdditivesCount === 0) {
    // 0 不等於「不含添加物」——可能是標示沒有辨識成功，系統分不出來。
    return '未比對到食品添加物。這可能是產品確實未使用，也可能是成分標示沒有辨識成功，請以包裝上的成分欄為準。';
  }
  const risky = highRiskCount > 0
    ? `其中 ${highRiskCount} 項對特定族群有風險提示。`
    : '目前沒有對特定族群的高風險提示。';
  return `比對到 ${totalAdditivesCount} 項食品添加物。${risky}各項的用途、每日容許攝取量與出處請點開清單查看。`;
}
