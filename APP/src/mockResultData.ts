/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { AnalysisResponse } from './types';

export const MOCK_RESULTS: Record<string, AnalysisResponse> = {
  TEST: {
    health_score: 15,
    risk_level: 'high',
    product_info: {
      name: '純喫茶香橙綠茶 (高風險安全特案)',
      brand: '純喫茶',
      manufacturer: '統一企業股份有限公司',
      barcode: 'TEST',
    },
    allergen_warnings: ['本生產線亦生產含麩質之穀物、大豆及牛奶製品的產品。高血壓及高血糖代謝族群、孕婦及幼兒請酌量飲用。'],
    score_breakdown: [
      { reason: '高含糖量與高升糖', description: '含有 29.2 公克添加糖，占每日建议攝取量之 58% (sugars 扣除分)', points: -7 },
      { reason: '熱量能量 (Calories)', description: '含有較多真空純化果糖能量負載', points: -3 },
    ],
    ingredients_detail: [
      {
        name: 'Dehydroacetic Acid (去水醋酸/水配製劑)',
        isAdditive: true,
        purpose: '防腐劑',
        description: '去水醋酸主要用於乾酪、起司、奶油及人造奶油限制防腐。具有強大抑菌能力，但嬰兒食品及一般飲料中禁止使用以避免累積肝腎負擔。',
        adiValue: '0 - 0.6 mg/kg body weight',
        iarcRating: '尚未分類危險級',
        groupRisks: [{ group: 'child', riskLevel: 4, reason: '極微量殘留對幼小童脆弱肝臟有負擔' }],
      },
      {
        name: 'Sucrose Fatty Acid Ester (蔗糖配製乳化劑)',
        isAdditive: true,
        purpose: '脂肪酸蔗糖酯乳化穩定劑',
        description: '食品加工優良用乳化劑。使果汁精油與茉莉茶油均質乳化不分層不泛油滴。正常用量對健康無礙。',
        adiValue: '0 - 20 mg/kg body weight',
        iarcRating: '無危害安全評定',
        groupRisks: [],
      },
      {
        name: 'DL-Malic Acid (DL-蘋果酸原料)',
        isAdditive: true,
        purpose: '酸味劑 / 鮮果提味',
        description: '蘋果及核果中廣泛天然存在的有機酸。修飾果汁甜度。法令規定嬰兒代乳與嬰兒食品禁用。',
        adiValue: '不限制',
        iarcRating: '無害分類',
        groupRisks: [],
      },
    ],
    food_safety_events: [
      {
        date: '2024-07',
        type: '消費者投訴',
        title: '老坛酸菜牛肉面中吃出鼠头？统一食品：正核實',
        summary: '網友於7月14日反映在統一老壇酸菜牛肉面中發現疑似鼠頭之異物並向市監局投訴，公司表示正與消費者核實中。',
        sources: {
          official: [
            { title: '衛福部食藥署公告', url: 'https://www.fda.gov.tw' },
          ],
          news: [
            { title: '聯合新聞網報導', url: 'https://udn.com' },
            { title: '自由時報報導', url: 'https://www.ltn.com.tw' },
          ],
          social: [
            { title: '社群討論串', url: 'https://www.wenxuecity.com/news/2024/07/16/socialnews-245486.html' },
          ],
        },
      },
    ],
  },
  '4710018123456': {
    health_score: 42,
    risk_level: 'medium',
    product_info: {
      name: '純喫茶香橙綠茶 (Orange Green Tea)',
      brand: '純喫茶',
      manufacturer: '統一企業股份有限公司',
      barcode: '4710018123456',
    },
    allergen_warnings: ['本生產線亦生產含麩質之穀物、大豆及牛奶製品。對咖啡因敏感患者、孕婦及幼兒請酌量飲用。'],
    score_breakdown: [
      { reason: '高含糖量 (Sugar)', description: '含有 29.2 公克添加糖，占每日建议攝取量之 58%', points: -8 },
      { reason: '熱量 (Calories)', description: '127 大卡能量，部分來自果糖', points: -2 },
      { reason: '添加抗氧化劑 (Vitamin C)', description: '維持維生素營養並阻礙綠茶變色褐色', points: 2 },
      { reason: '茶多酚含量 (Tea Polyphenols)', description: '天然茶多酚有利於抗自由基', points: 3 },
      { reason: '鈉含量低 (Sodium)', description: '微量電解質鈉，不造成腎臟負擔', points: 0 },
    ],
    ingredients_detail: [
      { name: '水', isAdditive: false, description: '加工基礎過濾純水' },
      { name: '蔗糖', isAdditive: false, description: '高能量碳水化合物，使果汁茶體圓潤甜美。過度食用易引起肥胖與牙齒蛀融。' },
      { name: '柳丁原汁', isAdditive: false, description: '提供極佳橙果香味與柳丁精油，含微量維生素C與微量果糖。' },
      { name: '柳橙濃縮汁', isAdditive: false, description: '真空低溫提純之濃縮柳橙漿，強化濃郁果香與厚實多汁口感。' },
      {
        name: '麥芽糊精',
        isAdditive: true,
        purpose: '穩定劑與乾爽填充劑',
        description: '多用於增甜與風味載體，雖然是一般修飾食品澱粉，但升糖指數(GI)偏高，糖尿病族群需審慎。',
        adiValue: '無限制',
        iarcRating: '未分類',
        groupRisks: [{ group: 'diabetes', riskLevel: 3, reason: '升糖指數極高，易增加胰島素負荷與血糖波動' }],
      },
    ],
    food_safety_events: [],
  },
};
