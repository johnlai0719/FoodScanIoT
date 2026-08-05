/**
 * Fog 端的型別定義。
 *
 * 2026-08-05 由 shared/types.ts 搬入（是搬移，不是複製——多一份副本正是先前
 * 三份 TypeScript 型別各自漂移的成因）。
 *
 * 搬移原因是建置錯誤：fog/tsconfig.json 的 rootDir 為 ./，而 `../shared/types`
 * 落在 rootDir 之外，`npm run build` 會以 TS6059 失敗。由於 shared/ 實際上只有
 * fog 在 import（App 有自己的 src/types.ts，web 不使用），「shared」這個名稱
 * 已名不副實，故直接移入使用端。
 *
 * 回應形狀的權威來源不在此處：
 *   - 產生處：server/module_d/response_builder.py 的 build_response()
 *   - 強制處：tests/contract/（欄位集合已凍結，改動會讓 CI 轉紅）
 *   - App 消費端：APP/src/types.ts 的 AnalysisResponse
 */

/**
 * 使用者健康條件。
 *
 * @deprecated 個人化已於 2026-08-04 移至 App 端本地計算，健康背景不再離開裝置。
 *             新版 App 不會送出此欄位；保留僅為相容舊版 App，且 Fog 的
 *             mask_sensitive_data() 一律將其剝除，不轉發至 Cloud。
 */
export interface UserConditions {
  /** 過敏原列表，例如 ["peanut", "egg"] */
  allergens: string[];
  /** 所屬族群，例如 "adult", "child", "pregnant" */
  group: string;
  /** 自定義過敏原文字 */
  custom_allergens?: string[];
  /** 自定義健康條件/族群 */
  custom_conditions?: string[];
  /** 慢性病設定，例如 ["hypertension", "diabetes"] */
  chronic_conditions?: string[];
}

/**
 * App 送往 Fog 的請求（POST /query）。
 * 新版 App 只送 barcode 與 label_images。
 */
export interface FogQueryRequest {
  /** 食品條碼 (EAN-8/13)，或測試字串 "TEST" */
  barcode: string;
  /** @deprecated 見 UserConditions 說明 */
  user_conditions?: UserConditions;
  /** 標籤影像陣列（Base64 字串） */
  label_images?: string[];
}

/**
 * Fog 從 Python 層取回的分析結果。
 *
 * 刻意只宣告 Fog **自己會讀取**的欄位，其餘以索引簽章帶過——完整的回應形狀由
 * Cloud 端產生並由契約測試把關，在此重複宣告只會製造第二份會過期的定義。
 *
 * 定義此型別是為了取代 `as any`：node-fetch 的 json() 回傳 unknown，直接展開或
 * 取屬性會編譯失敗。用具名型別而非 any，可讓「Fog 依賴哪些欄位」一目了然。
 */
export interface AnalysisResultLike {
  /** "success" / "error" / "degraded" 等；Fog 據此決定是否寫入快取 */
  status?: string;
  /** Fog 以此字串判斷 Cloud 是否處於降級狀態 */
  overall_summary?: string;
  [key: string]: unknown;
}
