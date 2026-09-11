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
   * Cloud 計算此分析的時間（ISO 8601），由 build_response() 產生、Fog 不會改動。
   * 快取命中時仍是原始計算時間——這正是要顯示給使用者的「上次更新」。
   */
  processed_at?: string;
  /** Fog 在 Cloud 不可用、改以過期快取回應時帶上的提示 */
  _warning?: string;
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

/**
 * Fog 本機降階回應：Cloud 連不上且無快取時，Fog 以本機 OCR 產出的部分結果。
 * 產生處是 fog/transforms.py 的 build_degraded_local_response()，
 * 欄位集合由 tests/contract/test_degraded_local_contract.py 與本型別比對。
 *
 * 沒有 health_score 是刻意的：現行解包邏輯遇到沒有 health_score 的回應會顯示 message，
 * 所以尚未支援降階的版本會安全地顯示說明文字，不會出現沒算過的分數。
 * 要呈現部分結果時，以 status === 'degraded' && degraded_mode === 'local_ocr' 判斷；
 * unavailable_fields 列出的區塊要顯示成「離線時無法提供」，不可當成 0 或「沒有」。
 * ingredients_detail 與 AnalysisResponse 的同名欄位同形，可共用同一套元件。
 */
export interface DegradedLocalResponse {
  status: 'degraded';
  degraded_mode: 'local_ocr';
  message: string;
  barcode: string | null;
  ingredients_detail: IngredientDetail[];
  unavailable_fields: string[];
  engine: Record<string, string>;
  processed_at: string;
  elapsed_s: number;
  /** Node 層（fog/queryHandler.ts）轉發時加上 */
  cached?: boolean;
}

export interface UserConditions {
  group: 'adult' | 'pregnant' | 'child' | 'hypertension' | 'diabetes';
  allergens: string[];
  chronic_conditions?: string[];
}
