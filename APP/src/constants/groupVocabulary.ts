/**
 * 族群風險詞彙 —— 與 Cloud 端保持一致的單一來源
 *
 * `CLOUD_GROUP_CODES` 是 `ingredients_detail[].groupRisks[].group` 可能出現的**全部**值，
 * 對應 server/module_a/ingredient_matching.py 的 `GROUP_ZH_TO_EN` 值域。
 * 兩邊的一致性由 tests/contract/test_group_vocabulary.py 強制：任一邊增刪都會讓測試轉紅。
 *
 * 注意：這份清單**不含 hypertension 與 diabetes**。資料庫裡「高血壓患者」等中文標籤不在
 * Cloud 的轉換表內，會在組裝回應時被靜默丟棄，所以高血壓／糖尿病的個人化只能靠營養素
 * 閾值（見 utils/personalization.ts 的 checkNutritionThresholds），不能靠 groupRisks。
 */

export const CLOUD_GROUP_CODES = [
  'pregnant',
  'child',
  'kidney_disease',
  'asthma',
  'aspirin_allergy',
  'pku',
  'allergy',
] as const;

export type CloudGroupCode = (typeof CLOUD_GROUP_CODES)[number];

/** 顯示用中文標籤。鍵必須涵蓋 CLOUD_GROUP_CODES 全部值（由型別強制）。 */
export const GROUP_LABELS_ZH: Record<CloudGroupCode, string> = {
  pregnant: '孕婦 / 哺乳期婦女',
  child: '嬰幼兒 / 兒童',
  kidney_disease: '慢性腎臟病患者',
  asthma: '氣喘患者',
  aspirin_allergy: '阿斯匹靈過敏者',
  pku: '苯酮尿症患者',
  allergy: '過敏體質者',
};

/** 取顯示標籤；遇到未知代碼時回傳原字串而非空白，避免資訊消失。 */
export function groupLabel(code: string): string {
  return GROUP_LABELS_ZH[code as CloudGroupCode] ?? code;
}
