/**
 * 本地個人化比對
 *
 * 2026-08-04 由 Fog 端 `calculate_personalized_score()` 移植而來。使用者的健康背景
 * （族群、慢性病、過敏原）自此完全不離開裝置——App 不再將 `user_conditions` 送往
 * Fog／Cloud，所有比對都在本地完成。
 *
 * 三種比對彼此獨立：
 *   1. 過敏原偵測（雙層：成分明細 + 標示大字串）
 *   2. 族群添加物風險（`groupRisks[].group` 命中使用者標籤）
 *   3. 慢性病營養閾值（鈉／糖）
 */

import { AnalysisResponse, UserConditions } from '../types';

// ─── 使用者輪廓 ────────────────────────────────────────────────────────────────

export interface PersonalProfile {
  /** 群體單選；'adult' 視為無特殊族群，不參與比對 */
  group: UserConditions['group'];
  /**
   * 過敏原。可含使用者自訂的自由文字——查不到擴展關鍵字時會直接以該文字比對，
   * 故法定 11 大類以外的過敏原（如木瓜）仍能命中。
   */
  allergens: string[];
  /**
   * 慢性病，僅接受預設代碼（hypertension / diabetes / hyperlipidemia）——三高。
   * 自訂中文文字無意義：groupRisks[].group 只會是 7 個英文碼，營養閾值也只認
   * 這兩個代碼，中文字串兩邊都對不上。UI 的自訂欄位已於 2026-08-05 移除。
   */
  chronicConditions: string[];
}

export interface AdditiveRisk {
  name: string;
  reason: string;
  group: string;
  riskLevel: number;
  /** 依據的出處。空字串代表那一筆沒有記，畫面上不要顯示成連結。 */
  sourceUrl?: string;
  sourceTitle?: string;
  sourceYear?: number | null;
  /** 未經人工複核時要在畫面上標出來——見 types.ts 的 GroupRisk。 */
  reviewedByHuman?: boolean;
}

export interface NutritionWarning {
  /** 觸發此警示的慢性病名稱，例如「高血壓」 */
  condition: string;
  title: string;
  detail: string;
}

export interface PersonalRisks {
  /** 命中的過敏原（中文標準名） */
  matchedAllergens: string[];
  additiveRisks: AdditiveRisk[];
  nutritionWarnings: NutritionWarning[];
}

// ─── 對照表（移植自 fog/main.py）──────────────────────────────────────────────

/** 過敏原標籤正規化：英文別名 → 中文標準名 */
const ALLERGEN_ALIASES: Record<string, string> = {
  milk: '牛奶', dairy: '牛奶',
  egg: '蛋', eggs: '蛋',
  nuts: '堅果', nut: '堅果',
  gluten: '含麩質穀物', wheat: '含麩質穀物',
  soy: '大豆', soybean: '大豆',
  crustacean: '甲殼類', shrimp: '甲殼類', crab: '甲殼類',
  fish: '魚類', peanut: '花生', sesame: '芝麻',
  sulfite: '亞硫酸鹽', mango: '芒果',
};

/**
 * 過敏原關鍵字擴展。英文關鍵字一律小寫（比對前會將來源文字轉小寫）。
 *
 * 已知限制（沿用 Fog 既有行為，未在本次遷移變更判定邏輯）：
 * 部分關鍵字過短會誤判，例如「卵」會命中大豆的卵磷脂，導致選取「蛋」過敏原時
 * 對含卵磷脂的產品誤報。若要收斂誤報需另案處理，屬於判定邏輯變更。
 */
const ALLERGEN_KEYWORDS: Record<string, string[]> = {
  牛奶: ['乳', '奶', '乳清', '酪蛋白', 'milk', 'dairy', 'lactose'],
  蛋: ['蛋', '卵', 'egg'],
  堅果: ['核桃', '腰果', '杏仁', '夏威夷豆', '榛果', '堅果', 'nut'],
  含麩質穀物: ['麵粉', '小麥', '大麥', '燕麥', '黑麥', '麩質', 'gluten', 'wheat'],
  // 原 fog/main.py 誤植為「卵聯脂」，此處更正為「卵磷脂」
  大豆: ['黃豆', '大豆', '卵磷脂', 'soy'],
  甲殼類: ['蝦', '蟹', '龍蝦', 'shrimp', 'crab'],
  魚類: ['魚', '明膠', 'fish'],
  花生: ['花生', '落花生', 'peanut'],
  芝麻: ['芝麻', '香油', 'sesame'],
  亞硫酸鹽: ['亞硫酸', '二氧化硫', '漂白劑', 'sulfite'],
  芒果: ['芒果', 'mango'],
};

/** 慢性病營養閾值。基準為每 100 公克／100 毫升，與 Cloud 的 nutrition_facts 一致。 */
const NUTRITION_RULES = [
  {
    conditionKeys: ['hypertension'],
    condition: '高血壓',
    field: 'sodium' as const,
    threshold: 400,
    unit: '毫克',
    title: '高鈉警示',
    // 國健署《成人高血壓防治指南》每日鈉上限 2400 毫克，400 毫克已達 1/6
    rationale: '超過每 100 公克 400 毫克的建議閾值',
  },
  {
    conditionKeys: ['diabetes'],
    condition: '高血糖',
    field: 'sugar' as const,
    threshold: 10,
    unit: '公克',
    title: '高糖警示',
    // WHO 2015 游離糖建議 <25 公克/日，10 公克已達 40%
    rationale: '超過每 100 公克 10 公克的建議閾值，對血糖波動影響顯著',
  },
  {
    // 2026-09-13 新增，補上三高的第三項。
    //
    // 在這之前做不到，原因不是沒有標準而是**拿不到值**：Cloud 的
    // nutrition_facts 只回五欄，飽和脂肪不在裡面（CLAUDE.md 記過）。
    // 同日把它補進 server/main.py 的 nutrition dict 與 22 欄契約，
    // 這條規則才成立。值仍然**很常是 None**（解析拿不到），
    // 而 checkNutritionThresholds 會跳過非數值——那是對的，
    // 「沒有標示」不可當成 0 而判為安全。
    conditionKeys: ['hyperlipidemia'],
    condition: '高血脂',
    field: 'saturated_fat' as const,
    threshold: 5,
    unit: '公克',
    title: '高飽和脂肪警示',
    // 英國 FSA 前標交通燈號：飽和脂肪 >5 公克/100 公克為紅燈（高）。
    // 刻意不用總脂肪：那會把富含不飽和脂肪的堅果與植物油一起警示，
    // 而它們對血脂的作用方向相反。
    rationale: '超過每 100 公克 5 公克的建議閾值，與血中低密度脂蛋白升高相關',
  },
];

/** Fog 舊版寫入的個人化結果格式；這些項目屬於「別的使用者」，一律不得採用 */
const STALE_PERSONALIZED_WARNING = /偵測到過敏原[：:]/;

// ─── 內部工具 ──────────────────────────────────────────────────────────────────

/** 將使用者輸入的過敏原正規化為中文標準名並去重 */
function normalizeAllergens(allergens: string[]): string[] {
  const normalized = allergens
    .map(a => a.trim())
    .filter(Boolean)
    .map(a => ALLERGEN_ALIASES[a.toLowerCase()] ?? a);
  return Array.from(new Set(normalized));
}

/** 使用者的族群/疾病標籤，用於比對 groupRisks[].group */
export function buildActiveTags(profile: PersonalProfile): string[] {
  const tags = [
    ...(profile.group !== 'adult' ? [profile.group] : []),
    ...profile.chronicConditions,
  ];
  return Array.from(new Set(tags.filter(Boolean)));
}

/**
 * 濾掉 Fog 舊版寫入的個人化過敏原項目。
 * 舊快取列可能挾帶前一位使用者的比對結果，顯示出來是錯的。
 */
export function getProductAllergenWarnings(result: AnalysisResponse | null): string[] {
  return (result?.allergen_warnings ?? []).filter(w => !STALE_PERSONALIZED_WARNING.test(w));
}

// ─── 1. 過敏原偵測（雙層）─────────────────────────────────────────────────────

export function detectAllergens(
  result: AnalysisResponse | null,
  allergens: string[],
): string[] {
  if (!result) return [];
  const userAllergens = normalizeAllergens(allergens);
  if (userAllergens.length === 0) return [];

  const matched: string[] = [];

  // 第一層：比對成分明細的名稱
  const ingredients = result.ingredients_detail ?? [];
  for (const allergen of userAllergens) {
    const keywords = ALLERGEN_KEYWORDS[allergen] ?? [allergen];
    const hit = ingredients.some(ing => {
      const name = (ing?.name ?? '').toLowerCase();
      return name !== '' && keywords.some(k => name.includes(k));
    });
    if (hit) matched.push(allergen);
  }

  // 第二層：比對標示大字串（防止 AI 未正確拆解成分）
  // 來源不含舊版個人化項目，避免把別人的比對結果當成本產品的標示。
  const nested = result.data ?? {};
  const sourceText = [
    nested.product_info?.ingredients ?? '',
    nested.product_info?.allergens ?? '',
    ...Object.keys(nested.ingredient_types ?? {}),
    ...getProductAllergenWarnings(result),
  ].join(' ').toLowerCase();

  if (sourceText.trim() !== '') {
    for (const allergen of userAllergens) {
      if (matched.includes(allergen)) continue;
      const keywords = ALLERGEN_KEYWORDS[allergen] ?? [allergen];
      if (keywords.some(k => sourceText.includes(k))) matched.push(allergen);
    }
  }

  return matched;
}

// ─── 2. 族群添加物風險 ────────────────────────────────────────────────────────

/**
 * 比對添加物的族群風險標記。
 *
 * 注意：Cloud 端的 `groupRisks[].group` 只會是 pregnant／child／kidney_disease／
 * asthma／aspirin_allergy／pku／allergy 七種英文值（見 server/module_a/
 * ingredient_matching.py 的 GROUP_ZH_TO_EN），永遠不含 hypertension／diabetes——
 * 那兩者只能靠 checkNutritionThresholds() 觸發。
 *
 * 與 Fog 的差異：Fog 另外要求 riskLevel >= 3，此處刻意不過濾。資料庫 65 筆風險中
 * 有 42 筆是 caution(level 1)，套用該門檻會讓它們全部消失。
 */
export function matchAdditiveRisks(
  result: AnalysisResponse | null,
  activeTags: string[],
): AdditiveRisk[] {
  if (!result || activeTags.length === 0) return [];

  return (result.ingredients_detail ?? [])
    .filter(i => i.isAdditive === true || i.isAdditive === 'true')
    .flatMap(ing =>
      (ing.groupRisks ?? [])
        .filter(r => activeTags.includes(r.group))
        .map(r => ({
          name: ing.name,
          reason: r.reason,
          group: r.group,
          riskLevel: r.riskLevel,
          // 出處一併帶下去。不帶的話畫面講得出理由卻講不出依據，
          // 使用者無從分辨這句話是查來的還是編的。
          sourceUrl: r.sourceUrl,
          sourceTitle: r.sourceTitle,
          sourceYear: r.sourceYear,
          reviewedByHuman: r.reviewedByHuman,
        })),
    );
}

// ─── 3. 慢性病營養閾值 ────────────────────────────────────────────────────────

export function checkNutritionThresholds(
  result: AnalysisResponse | null,
  activeTags: string[],
): NutritionWarning[] {
  const nutrition = result?.data?.nutrition_facts;
  if (!nutrition || activeTags.length === 0) return [];

  const warnings: NutritionWarning[] = [];
  for (const rule of NUTRITION_RULES) {
    if (!rule.conditionKeys.some(k => activeTags.includes(k))) continue;

    const value = nutrition[rule.field];
    // 標示缺該項時為 null／undefined，不可當成 0 判定
    if (typeof value !== 'number' || Number.isNaN(value)) continue;
    if (value <= rule.threshold) continue;

    warnings.push({
      condition: rule.condition,
      title: rule.title,
      detail: `每 100 公克含 ${value} ${rule.unit}，${rule.rationale}。`,
    });
  }
  return warnings;
}

// ─── 統一進入點 ────────────────────────────────────────────────────────────────

export function analyzePersonalRisks(
  result: AnalysisResponse | null,
  profile: PersonalProfile,
): PersonalRisks {
  const activeTags = buildActiveTags(profile);
  return {
    matchedAllergens: detectAllergens(result, profile.allergens),
    additiveRisks: matchAdditiveRisks(result, activeTags),
    nutritionWarnings: checkNutritionThresholds(result, activeTags),
  };
}
