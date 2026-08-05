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
 * [Endpoint 2] Fog 回傳給 App 的結果
 *
 * 這裡原本有一個 `FogQueryResult` 介面，已於 2026-08-04 移除。移除原因：
 * 它被 queryHandler.ts import 但從未實際套用在任何值上（所有回傳路徑都是
 * `data: any`），而且已與實際回應嚴重脫節——真正產出的 22 個頂層欄位裡，
 * 它只列了 9 個，且缺少 App 真正依賴的 product_info / score_breakdown /
 * ingredients_detail / 三個 summary。留著一份沒人檢查又不正確的型別，比沒有更糟。
 *
 * 回應形狀的權威來源與強制點：
 *   - 產生處：server/module_d/response_builder.py 的 build_response()
 *   - 強制處：tests/contract/test_cloud_response_contract.py（欄位集合已凍結）
 *   - App 消費端型別：APP/src/types.ts 的 AnalysisResponse
 */

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
