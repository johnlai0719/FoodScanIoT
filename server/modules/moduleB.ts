import { UserConditions } from '../../../shared/types';

export interface HealthRiskResult {
  health_score: number;
  risk_tags: string[];
  allergen_warnings: string[];
  score_breakdown: Record<string, number>;
}

/**
 * 模組 B：健康風險引擎
 */
export async function processModuleB(
  nutritionVector: any,
  matchedAdditives: any[],
  userConditions: UserConditions
): Promise<HealthRiskResult> {
  
  let score = 100;
  const breakdown: Record<string, number> = {};
  const tags: string[] = [];
  const warnings: string[] = [];

  const { group, allergens } = userConditions;

  // 1. 營養標示扣分邏輯
  const deduct = (key: string, value: number, limit: number, points: number, tag?: string) => {
    if (value > limit) {
      // 個人化加權
      let multiplier = 1;
      if (key === 'sodium' && group === 'hypertension') multiplier = 1.5;
      if (key === 'sugar' && group === 'diabetes') multiplier = 1.5;

      const totalDeduct = points * multiplier;
      score -= totalDeduct;
      breakdown[key] = totalDeduct;
      if (tag) tags.push(tag);
    }
  };

  deduct('sugar', nutritionVector.sugar_per100g, 10, 15, '高糖');
  deduct('sodium', nutritionVector.sodium_per100g, 600, 20, '高鈉');
  deduct('calories', nutritionVector.calories_per100g, 400, 10, '高熱量');
  deduct('saturated_fat', nutritionVector.saturated_fat_per100g, 5, 10, '高飽和脂肪');

  // 2. 添加物扣分與過敏原檢查
  matchedAdditives.forEach(add => {
    let multiplier = 1;
    if (group === 'pregnant' && add.risk_level === 'high') multiplier = 2;
    if (group === 'child') multiplier = 1.3;

    if (add.risk_level === 'high') {
      const d = 10 * multiplier;
      score -= d;
      breakdown[add.name_zh] = d;
      if (!tags.includes('含高風險添加物')) tags.push('含高風險添加物');
    } else if (add.risk_level === 'medium') {
      const d = 3 * multiplier;
      score -= d;
      breakdown[add.name_zh] = d;
    }

    // 過敏原比對 (簡單字串包含)
    allergens.forEach(all => {
      if (add.name_zh.includes(all) || add.input_name.includes(all)) {
        warnings.push(`產品含「${add.name_zh}」，與您的「${all}」過敏原相符`);
      }
    });
  });

  // 3. 分數限幅 (0-100)
  score = Math.max(0, Math.min(100, score));

  return {
    health_score: Math.round(score),
    risk_tags: tags,
    allergen_warnings: warnings,
    score_breakdown: breakdown
  };
}
