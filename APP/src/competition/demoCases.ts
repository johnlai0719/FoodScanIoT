import { AnalysisResponse } from '../types';

// Synthetic fixtures: never used by normal barcode/photo submission or saved as scan history.
export const COMPETITION_DEMO_CASES: { id: string; title: string; description: string; result: AnalysisResponse }[] = [
  {
    id: 'drink', title: '案例一：營養與添加物解釋', description: '預覽營養圓環、用途分類與成分詳情。',
    result: {
      health_score: 5, nutri_grade: 'C', risk_level: 'medium', score_scale: 'nutriscore_points_lower_is_better',
      product_info: { name: '示範果味飲料（虛構案例）', brand: '', manufacturer: '比賽介面示範，非真實商品', barcode: 'DEMO-DRINK' },
      allergen_warnings: [], food_safety_events: [],
      score_breakdown: [
        { key: 'sugars', reason: '糖', description: '這筆示範設定糖的扣分為 4 分，用來展示圓環與說明入口。', points: -4, value: 10, unit: 'g', maxPoints: 10, kind: 'penalty' },
        { key: 'energy', reason: '熱量', description: '這筆示範設定熱量的扣分為 1 分。並非實際食品的計分結果。', points: -1, value: 44, unit: 'kcal', maxPoints: 10, kind: 'penalty' },
      ],
      ingredients_detail: [
        { name: '水', isAdditive: false, description: '包裝成分欄位的示範資料。' },
        { name: '砂糖', isAdditive: false, description: '包裝成分欄位的示範資料。' },
        { name: '檸檬酸', isAdditive: true, category: ['調味劑'], purpose: '調整酸味', description: '用途欄位的介面示例；本案例未執行資料庫查詢。', groupRisks: [] },
        { name: '果膠', isAdditive: true, category: ['粘稠劑'], purpose: '調整口感', description: '展示另一類添加物，供操作成分詳情。', groupRisks: [] },
      ],
      data: { nutrition_facts: { calories: 44, sugar: 10, sodium: 12, saturated_fat: 0, protein: 0 } },
    },
  },
  {
    id: 'allergen', title: '案例二：個人化過敏原提醒', description: '健康設定選擇「牛奶」後，預覽警示與相同結果版面。',
    result: {
      health_score: 0, nutri_grade: 'B', risk_level: 'low', score_scale: 'nutriscore_points_lower_is_better',
      product_info: { name: '示範含乳點心（虛構案例）', brand: '', manufacturer: '比賽介面示範，非真實商品', barcode: 'DEMO-MILK' },
      allergen_warnings: ['本示範包裝標示含牛奶。'], food_safety_events: [],
      score_breakdown: [],
      ingredients_detail: [
        { name: '牛奶', isAdditive: false, description: '用於驗證個人設定與包裝過敏原提示的示範成分。' },
        { name: '水', isAdditive: false, description: '包裝成分欄位的示範資料。' },
      ],
      data: { nutrition_facts: { calories: 60, sugar: 4, sodium: 40, saturated_fat: 1, protein: 3 } },
    },
  },
];

export type CompetitionDemoMode = 'cloud' | 'miss' | 'cache' | 'queue';
export const COMPETITION_DEMO_MODES: { id: CompetitionDemoMode; title: string; description: string; bypassCache: boolean }[] = [
  { id: 'miss', title: '1. 首次掃描（Cache Miss）', bypassCache: false, description: '使用 Fog 尚未快取的條碼與實際照片。由 Fog 回報是否真的未命中；若已命中，儀表板會如實顯示 Hit。' },
  { id: 'cache', title: '2. 重複掃描（Cache Hit）', bypassCache: false, description: '再次掃描已完成分析的同一條碼；若 Fog 有快取便直接回傳，實際是否命中依回覆顯示。' },
  { id: 'queue', title: '3. 離線暫存與補送', bypassCache: false, description: '手機需仍能連上 Fog。請讓 Fog 暫時無法連雲端，再掃描未快取的案例；若有快取仍會優先回傳。收到暫存確認後，恢復雲端並等待結果。' },
  { id: 'cloud', title: '重新分析（Bypass）', bypassCache: true, description: '透過 Fog 重新辨識，不讀取快取。請拍攝實際標示，再按「開始分析」。' },
];
