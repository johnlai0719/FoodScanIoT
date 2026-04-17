import { FogQueryResult, UserConditions, ProductInfo } from '../../../shared/types';
import { HealthRiskResult } from './moduleB';
import { FoodSafetyResult } from './moduleC';

/**
 * 模組 D：融合與可解釋輸出
 */
export function processModuleD(
  barcode: string,
  productInfo: ProductInfo,
  moduleB: HealthRiskResult,
  moduleC: FoodSafetyResult,
  userConditions: UserConditions
): FogQueryResult {
  
  // 1. 綜合風險等級
  let overallRisk: 'low' | 'medium' | 'high' = 'low';
  if (moduleB.health_score < 40 || moduleC.food_safety_level === 'critical' || moduleC.food_safety_level === 'high') {
    overallRisk = 'high';
  } else if (moduleB.health_score < 65 || moduleC.food_safety_level === 'medium') {
    overallRisk = 'medium';
  }

  // 2. 判定依據生成
  const triggers: string[] = [];
  Object.entries(moduleB.score_breakdown).forEach(([key, val]) => {
    if (val > 0) triggers.push(`扣分項: ${key} (-${val}分)`);
  });

  const sources = [
    "食品添加物知識庫 v1.2",
    "TFDA 衛福部公告 2024",
    `系統知識庫版本: ${process.env.KNOWLEDGE_VERSION || '2026.03.12'}`
  ];

  // 3. 個人化建議
  const notes: string[] = [];
  if (userConditions.group === 'hypertension' && moduleB.risk_tags.includes('高鈉')) {
    notes.push("本品鈉含量偏高，建議高血壓族群避免食用。");
  }
  if (userConditions.group === 'diabetes' && moduleB.risk_tags.includes('高糖')) {
    notes.push("本品含糖量偏高，請糖尿病族群留意攝取量。");
  }
  if (userConditions.group === 'pregnant' && moduleB.risk_tags.includes('含高風險添加物')) {
    notes.push("本品含高風險添加物，建議懷孕期間減少攝取。");
  }

  return {
    barcode,
    health_score: moduleB.health_score,
    risk_level: overallRisk,
    risk_tags: moduleB.risk_tags,
    allergen_warnings: moduleB.allergen_warnings,
    food_safety_events: moduleC.events,
    explanation: {
      triggers: triggers.slice(0, 5),
      sources
    },
    personalized_notes: notes.length > 0 ? notes : ["目前無特定健康風險提醒"],
    processed_at: new Date().toISOString()
  };
}
