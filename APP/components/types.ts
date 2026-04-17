export enum IARCCategory {
  GROUP_1 = "1",
  GROUP_2A = "2A",
  GROUP_2B = "2B",
  GROUP_3 = "3",
}

export interface GroupRisk {
  group: string; // e.g., "孕婦", "幼童", "高血壓患者"
  riskLevel: 1 | 2 | 3 | 4 | 5;
  reason: string;
}

export interface Ingredient {
  name: string;
  isAdditive: boolean;
  isAllergen: boolean;
  iarcRating?: IARCCategory;
  adiValue?: number; // mg per kg of body weight
  description: string;
  groupRisks?: GroupRisk[];
}

export interface ProductInfo {
  barcode: string;
  name: string;
  brand: string;
  manufacturer: string;
  allergens?: string;
  processingLevel: string;
  imageUrl?: string;
}

export interface HealthDiagnosis {
  grade: "A" | "B" | "C" | "D" | "E";
  score: number;
  transparencyScore: number;
  summary: string;
  warnings: string[];
  groupRiskSummary?: GroupRisk[];
  score_breakdown?: any[];
}

export interface NutritionInfo {
  calories: number;
  protein: number;
  fat: number;
  carbohydrates: number;
  sugar: number;
  sodium: number;
  servingSize: string;
}

export interface ScanResult {
  product_info: ProductInfo;
  ingredients_detail: Ingredient[];
  nutrition_facts: NutritionInfo;
  final_health_diagnosis: HealthDiagnosis;
  manufacturer_alerts?: any[];
  certification_marks?: string[];
  source: string;
  latency: number;
}

export type ConnectionStatus = "已連線" | "連線中" | "已斷線";

export interface SystemStatus {
  app: ConnectionStatus;
  fog: ConnectionStatus;
  cloud: ConnectionStatus;
}
