import { pool } from '../server';

export interface NormalizedResult {
  normalized_ingredients: string[];
  matched_additives: Array<{
    input_name: string;
    additive_id: string | null;
    name_zh: string;
    risk_level: string;
    purpose: string;
  }>;
  nutrition_vector: {
    sugar_per100g: number;
    sodium_per100g: number;
    calories_per100g: number;
    fat_per100g: number;
    saturated_fat_per100g: number;
  };
}

/**
 * 文字正規化：統一全半形、移除空白與括號內容
 */
function normalizeText(text: string): string {
  return text
    .replace(/[\uff01-\uff5e]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0)) // 全形轉半形
    .replace(/\s+/g, '') // 移除空白
    .replace(/\(.*?\)/g, '') // 移除括號內容
    .replace(/（.*?）/g, '')
    .trim();
}

/**
 * 模組 A：前處理與實體對齊
 */
export async function processModuleA(
  ingredients: string[], 
  additives: string[], 
  nutrition: any
): Promise<NormalizedResult> {
  
  // 1. 正規化成份文字
  const normalizedIngredients = ingredients.map(normalizeText);
  const normalizedAdditivesInput = additives.map(normalizeText);

  // 2. 同義詞與實體對齊 (查詢資料庫)
  const matchedAdditives = [];
  const [knowledgeRows] = await pool.query('SELECT * FROM additives_knowledge');
  const knowledge = knowledgeRows as any[];

  for (const input of normalizedAdditivesInput) {
    let match = knowledge.find(k => {
      const synonyms = JSON.parse(k.synonyms || '[]');
      return k.name_zh === input || k.name_en?.toLowerCase() === input.toLowerCase() || synonyms.includes(input);
    });

    // 若無完全比對，嘗試包含比對
    if (!match) {
      match = knowledge.find(k => k.name_zh.includes(input) || input.includes(k.name_zh));
    }

    matchedAdditives.push({
      input_name: input,
      additive_id: match ? match.additive_id : null,
      name_zh: match ? match.name_zh : '未知添加物',
      risk_level: match ? match.risk_level : 'low',
      purpose: match ? match.purpose : '未知'
    });
  }

  // 3. 營養向量轉化
  const nutritionVector = {
    sugar_per100g: parseFloat(nutrition.sugar) || 0,
    sodium_per100g: parseFloat(nutrition.sodium) || 0,
    calories_per100g: parseFloat(nutrition.calories) || 0,
    fat_per100g: parseFloat(nutrition.fat) || 0,
    saturated_fat_per100g: parseFloat(nutrition.saturated_fat) || 0
  };

  return {
    normalized_ingredients: normalizedIngredients,
    matched_additives: matchedAdditives,
    nutrition_vector: nutritionVector
  };
}
