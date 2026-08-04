/**
 * 本地個人化比對的回歸測試
 *
 * 過敏原偵測是安全相關邏輯——漏報的後果是使用者吃到會過敏的東西，誤報則會造成
 * 警訊疲勞而讓人開始忽略警示。這份測試在 2026-08-04 把邏輯從 Fog 移至 App 時建立。
 */

import {
  analyzePersonalRisks,
  buildActiveTags,
  checkNutritionThresholds,
  detectAllergens,
  getProductAllergenWarnings,
  matchAdditiveRisks,
  PersonalProfile,
} from '../personalization';
import { AnalysisResponse } from '../../types';

/** 依 Cloud build_response() 的真實形狀構造 */
const makeResult = (over: Partial<AnalysisResponse> = {}): AnalysisResponse => ({
  health_score: 62,
  risk_level: 'medium',
  score_breakdown: [],
  product_info: { name: '測試飲料', brand: 'B', manufacturer: 'M' },
  allergen_warnings: ['本產品含有牛奶及大豆製品。'],
  food_safety_events: [],
  ingredients_detail: [
    {
      name: '麥芽糊精', isAdditive: true, description: '',
      groupRisks: [{ group: 'diabetes', riskLevel: 3, reason: '升糖指數極高' }],
    },
    {
      name: '去水醋酸', isAdditive: true, description: '',
      groupRisks: [
        { group: 'child', riskLevel: 4, reason: '對幼小童脆弱肝臟有負擔' },
        { group: 'asthma', riskLevel: 1, reason: '可能誘發氣喘' },
      ],
    },
    { name: '花生粉', isAdditive: false, description: '' },
  ],
  data: {
    nutrition_facts: { calories: 210, protein: 0.5, fat: 0, sugar: 29.2, sodium: 480 },
  },
  ...over,
});

const ADULT: PersonalProfile = { group: 'adult', allergens: [], chronicConditions: [] };

describe('未設定任何健康條件', () => {
  it('不觸發任何個人化警示', () => {
    const r = analyzePersonalRisks(makeResult(), ADULT);
    expect(r.matchedAllergens).toEqual([]);
    expect(r.additiveRisks).toEqual([]);
    expect(r.nutritionWarnings).toEqual([]);
  });

  it('result 為 null 時不崩潰', () => {
    const r = analyzePersonalRisks(null, {
      ...ADULT, allergens: ['牛奶'], chronicConditions: ['diabetes'],
    });
    expect(r.matchedAllergens).toEqual([]);
    expect(r.additiveRisks).toEqual([]);
    expect(r.nutritionWarnings).toEqual([]);
  });
});

describe('慢性病營養閾值', () => {
  // 這是遷移前完全無法觸發的情境：groupRisks 的值域不含 hypertension/diabetes，
  // 使用者勾了高血壓卻什麼都不會發生。
  it('高血壓 + 鈉 480mg 觸發高鈉警示並帶出實際數值', () => {
    const r = analyzePersonalRisks(makeResult(), {
      ...ADULT, chronicConditions: ['hypertension'],
    });
    expect(r.nutritionWarnings).toHaveLength(1);
    expect(r.nutritionWarnings[0].condition).toBe('高血壓');
    expect(r.nutritionWarnings[0].detail).toContain('480');
  });

  it('糖尿病 + 糖 29.2g 觸發高糖警示', () => {
    const r = analyzePersonalRisks(makeResult(), {
      ...ADULT, chronicConditions: ['diabetes'],
    });
    expect(r.nutritionWarnings.map(w => w.condition)).toEqual(['糖尿病']);
  });

  it('未超過閾值時不觸發', () => {
    const r = analyzePersonalRisks(
      makeResult({ data: { nutrition_facts: { sodium: 120, sugar: 2 } } }),
      { ...ADULT, chronicConditions: ['hypertension', 'diabetes'] },
    );
    expect(r.nutritionWarnings).toEqual([]);
  });

  it('營養值為 null 時不得當成 0，也不得誤判為超標', () => {
    const r = checkNutritionThresholds(
      makeResult({ data: { nutrition_facts: { sodium: null, sugar: null } } }),
      ['hypertension', 'diabetes'],
    );
    expect(r).toEqual([]);
  });

  it('缺少 data 區塊時不崩潰', () => {
    const r = checkNutritionThresholds(makeResult({ data: undefined }), ['hypertension']);
    expect(r).toEqual([]);
  });

  it('未勾選慢性病時即使超標也不觸發', () => {
    expect(checkNutritionThresholds(makeResult(), [])).toEqual([]);
  });
});

describe('族群添加物風險', () => {
  it('幼童族群命中 child 風險', () => {
    const r = analyzePersonalRisks(makeResult(), { ...ADULT, group: 'child' });
    expect(r.additiveRisks.map(x => x.group)).toEqual(['child']);
    expect(r.additiveRisks[0].name).toBe('去水醋酸');
  });

  it('不命中使用者未設定的族群', () => {
    const r = analyzePersonalRisks(makeResult(), { ...ADULT, group: 'child' });
    expect(r.additiveRisks.some(x => x.group === 'asthma')).toBe(false);
  });

  // 刻意與 Fog 分歧：Fog 用 riskLevel >= 3 過濾，會讓資料庫 65 筆風險中的 42 筆
  // caution(level 1) 全部消失。App 選擇全部揭露。
  it('保留 riskLevel=1 的項目（不套用 Fog 的 >=3 門檻）', () => {
    const r = matchAdditiveRisks(makeResult(), ['asthma']);
    expect(r).toHaveLength(1);
    expect(r[0].riskLevel).toBe(1);
  });

  it('只比對添加物，不比對一般成分', () => {
    const result = makeResult({
      ingredients_detail: [{
        name: '天然香料', isAdditive: false, description: '',
        groupRisks: [{ group: 'child', riskLevel: 5, reason: '測試' }],
      }],
    });
    expect(matchAdditiveRisks(result, ['child'])).toEqual([]);
  });

  it("buildActiveTags 排除 'adult'，納入慢性病", () => {
    expect(buildActiveTags({
      group: 'adult', allergens: [], chronicConditions: ['hypertension'],
    })).toEqual(['hypertension']);
    expect(buildActiveTags({ ...ADULT, group: 'pregnant' })).toEqual(['pregnant']);
    expect(buildActiveTags({ ...ADULT, group: 'child', chronicConditions: ['diabetes'] }))
      .toEqual(['child', 'diabetes']);
  });
});

describe('過敏原偵測', () => {
  it('第一層：由成分明細名稱命中', () => {
    const r = detectAllergens(makeResult(), ['花生']);
    expect(r).toEqual(['花生']);
  });

  it('第二層：由標示大字串命中', () => {
    expect(detectAllergens(makeResult(), ['牛奶'])).toEqual(['牛奶']);
  });

  it('英文別名正規化為中文標準名', () => {
    expect(detectAllergens(makeResult(), ['milk'])).toEqual(['牛奶']);
    expect(detectAllergens(makeResult(), ['MILK'])).toEqual(['牛奶']);
  });

  it('未選取的過敏原不誤報', () => {
    expect(detectAllergens(makeResult(), ['芒果'])).toEqual([]);
  });

  it('同一過敏原不重複列出', () => {
    // 牛奶同時可由成分與標示文字命中，結果仍應只有一筆
    const result = makeResult({
      ingredients_detail: [{ name: '全脂奶粉', isAdditive: false, description: '' }],
    });
    expect(detectAllergens(result, ['牛奶'])).toEqual(['牛奶']);
  });
});

describe('舊快取的使用者汙染', () => {
  // Fog 舊版會把「偵測到過敏原：X」寫進 allergen_warnings 並存入快取，
  // 下一位使用者讀到的是前一位的比對結果。
  const dirty = makeResult({
    allergen_warnings: ['偵測到過敏原：芒果', '本產品含有牛奶及大豆製品。'],
  });

  it('通用警告濾除舊版個人化項目', () => {
    const warnings = getProductAllergenWarnings(dirty);
    expect(warnings).toEqual(['本產品含有牛奶及大豆製品。']);
  });

  it('不因舊快取殘留而誤報他人的過敏原', () => {
    expect(detectAllergens(dirty, ['芒果'])).toEqual([]);
  });

  it('本次使用者自己的過敏原仍能正常命中', () => {
    expect(detectAllergens(dirty, ['牛奶'])).toEqual(['牛奶']);
  });
});
