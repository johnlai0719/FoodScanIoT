/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

export interface GroupRisk {
  group: string;
  riskLevel: number;
  reason: string;
}

export interface IngredientDetail {
  name: string;
  isAdditive: boolean | string;
  description: string;
  purpose?: string;
  adiValue?: string;
  iarcRating?: string;
  groupRisks?: GroupRisk[];
  description_sources?: string[];
}

export interface ProductInfo {
  name: string;
  brand: string;
  manufacturer: string;
  barcode?: string;
}

export interface SourceLink {
  title: string;
  url: string;
}

export interface FoodSafetyEvent {
  date: string;
  type: string;
  title: string;
  summary: string;
  sources?: {
    official?: SourceLink[] | string | null;
    news?: SourceLink[] | string | null;
    social?: SourceLink[] | string | null;
  };
  /** @deprecated 舊格式相容，新資料請用 sources */
  links?: SourceLink[];
  source_url?: string;
}

export interface ScoreBreakdownItem {
  reason: string;
  description: string;
  points: number;
  type?: string;
}

/**
 * 營養標示。基準為「每 100 公克／100 毫升」——Cloud 端的 Vision prompt 明確只取
 * 標示表中「每100公克/毫升」那一欄（server/main.py 的 nutrition 欄位）。
 * 欄位可能為 null（該產品標示缺該項），使用前務必判空。
 */
export interface NutritionFacts {
  calories?: number | null;
  protein?: number | null;
  fat?: number | null;
  /** 公克 (g) */
  sugar?: number | null;
  /** 毫克 (mg) */
  sodium?: number | null;
}

export interface AnalysisResponse {
  health_score: number;
  risk_level: 'low' | 'medium' | 'high';
  score_breakdown: ScoreBreakdownItem[] | Record<string, number>;
  product_info?: ProductInfo;
  allergen_warnings: string[];
  food_safety_events: FoodSafetyEvent[];
  ingredients_detail: IngredientDetail[];
  overall_summary?: string;
  additives_summary?: string;
  safety_events_summary?: string;
  cached?: boolean;
  /**
   * Cloud 回應的巢狀區塊。`nutrition_facts` 只存在於此處，頂層沒有。
   * 詳見 server/module_d/response_builder.py 的 build_response()。
   */
  data?: {
    nutrition_facts?: NutritionFacts;
    product_info?: ProductInfo & { ingredients?: string; allergens?: string };
    ingredients_detail?: IngredientDetail[];
    ingredient_types?: Record<string, unknown>;
  };
}

export interface UserConditions {
  group: 'adult' | 'pregnant' | 'child' | 'hypertension' | 'diabetes';
  allergens: string[];
  chronic_conditions?: string[];
}
