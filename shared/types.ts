/**
 * 使用者健康條件設定
 */
export interface UserConditions {
  /** 過敏原列表，例如 ["peanut", "egg"] */
  allergens: string[];
  /** 所屬族群，例如 "adult", "child", "pregnant", "hypertension", "diabetes" */
  group: string;
  /** 自定義過敏原文字 */
  custom_allergens?: string[];
  /** 自定義健康條件/族群 */
  custom_conditions?: string[];
  /** 慢性病設定，例如 ["hypertension", "diabetes"] */
  chronic_conditions?: string[];
}

/**
 * 食安事件紀錄
 */
export interface FoodSafetyEvent {
  /** 事件日期 (ISO 8601) */
  date: string;
  /** 事件類型，例如 "抽驗不合格", "回收", "違規" */
  type: string;
  /** 事件摘要描述 */
  summary: string;
  /** 來源連結 (可選) */
  source_url?: string;
}

/**
 * 產品基本資訊
 */
export interface ProductInfo {
  /** 產品名稱 */
  name: string;
  /** 品牌 */
  brand: string;
  /** 製造商 */
  manufacturer: string;
  /** 產地 (可選) */
  origin?: string;
}

/**
 * 可解釋依據
 */
export interface Explanation {
  /** 觸發風險的關鍵因素列表 */
  triggers: string[];
  /** 引用來源列表 (例如衛福部、知識庫版本) */
  sources: string[];
}

/**
 * [Endpoint 1] POST /fog/query
 * App 發送給 Fog 的原始請求
 */
export interface FogQueryRequest {
  /** 食品條碼 (EAN-8/13) */
  barcode: string;
  /**
   * 使用者個人化條件
   * @deprecated 個人化已於 2026-08-04 移至 App 端本地計算，健康背景不再離開裝置。
   *             新版 App 不會送出此欄位；保留為選填僅為相容舊版 App。
   */
  user_conditions?: UserConditions;
  /** 標籤影像陣列 (Base64 字串，用於 OCR 備援與多角度分析) */
  label_images?: string[];
}

/**
 * [Endpoint 2] GET /fog/result/:barcode
 * Fog 回傳給 App 的最終處理結果
 */
export interface FogQueryResult {
  /** 條碼 */
  barcode: string;
  /** 健康評分 (0-100) */
  health_score: number;
  /** 風險等級 */
  risk_level: 'low' | 'medium' | 'high';
  /** 風險標籤，例如 ["高糖", "高鈉", "含反式脂肪"] */
  risk_tags: string[];
  /** 過敏原警告訊息 */
  allergen_warnings: string[];
  /** 相關食安事件紀錄 */
  food_safety_events: FoodSafetyEvent[];
  /** 可解釋的判定依據 */
  explanation: Explanation;
  /** 針對使用者的建議筆記 */
  personalized_notes: string[];
  /** 資料處理時間戳 */
  processed_at: string;
}

/**
 * [Endpoint 3] POST /cloud/analyze
 * Fog 轉發給 Cloud 進行深度 AI 分析的 Payload
 */
export interface CloudAnalyzeRequest {
  /** 條碼 */
  barcode: string;
  /** 從資料庫或 OCR 取得的產品基本資訊 */
  product_info: ProductInfo;
  /** 成份列表 */
  ingredients: string[];
  /** 添加物列表 */
  additives: string[];
  /** 營養標示資料 (JSON 格式) */
  nutrition: Record<string, any>;
  /** 歷史食安紀錄 */
  food_safety_records: FoodSafetyEvent[];
  /**
   * 使用者個人化條件
   * @deprecated Fog 的 mask_sensitive_data() 一律剝除此欄位，Cloud 實際上永遠收不到。
   *             宣告為選填以與執行期行為一致。
   */
  user_conditions?: UserConditions;
}
