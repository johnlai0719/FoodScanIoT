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
}

export interface UserConditions {
  group: 'adult' | 'pregnant' | 'child' | 'hypertension' | 'diabetes';
  allergens: string[];
  chronic_conditions?: string[];
}
