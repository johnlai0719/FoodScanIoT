// FoodScan — 前後端共用資料型別
// 後端回傳的 JSON 結構必須與此介面完全對齊（欄位名稱、型別）

export interface GroupRisk {
  group: string;
  riskLevel: number;   // 1–5，>= 3 前端標示高風險
  reason: string;
}

export interface IngredientDetail {
  name: string;
  isAdditive: boolean | string;  // true = 添加物（顯示紅色警示框）
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
  date: string;       // "YYYY-MM" 或 "YYYY-MM-DD"
  type: string;       // 含「違規/不合格/超標」→ 紅；含「稽查/例行/合格」→ 綠；其餘 → 黃
  title: string;
  summary: string;
  sources?: {
    official?: SourceLink[];  // 官方（藍色）
    news?: SourceLink[];      // 新聞（琥珀色）
    social?: SourceLink[];    // 社群（紫色）
  };
  /** @deprecated 舊格式相容，新資料請用 sources */
  links?: SourceLink[];
  source_url?: string;
}

export interface ScoreBreakdownItem {
  reason: string;       // 顯示為標題
  description: string;  // 顯示為說明
  points: number;       // 正數加分、負數扣分
}

export interface AnalysisResponse {
  health_score: number;                          // 0–100
  risk_level: 'low' | 'medium' | 'high';
  score_breakdown: ScoreBreakdownItem[] | Record<string, number>;  // 陣列或 key-value 皆接受
  product_info?: ProductInfo;                    // 查無產品時可省略
  allergen_warnings: string[];                   // 無警示時回傳 []
  food_safety_events: FoodSafetyEvent[];         // 無事件時回傳 []
  ingredients_detail: IngredientDetail[];
  overall_summary?: string;       // 產品整體 AI 摘要（顯示於產品卡片底部）
  additives_summary?: string;     // 添加物 AI 摘要（顯示於添加物 gateway 卡片）
  safety_events_summary?: string; // 食安事件 AI 摘要（顯示於歷史 gateway 卡片）
}

export interface UserConditions {
  group: 'adult' | 'pregnant' | 'child' | 'hypertension' | 'diabetes';
  allergens: string[];
}
