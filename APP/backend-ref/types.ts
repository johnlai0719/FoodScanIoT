// FoodScan — 前後端共用資料型別
//
// 這個檔案原本是 ../src/types.ts 的手抄副本，兩份已經各自漂移：src 端多了
// NutritionFacts、AnalysisResponse.data、UserConditions.chronic_conditions 等欄位，
// 這裡卻停在舊版。一份「宣稱是契約」但其實不正確的文件，比沒有文件更危險。
//
// 2026-08-04 起改為直接轉出 App 實際使用的型別，物理上不可能再漂移。
// 要看欄位定義與註解，請直接讀 ../src/types.ts。
//
// 相關的強制機制：
//   - Cloud 回應的欄位集合由 tests/contract/test_cloud_response_contract.py 凍結
//   - 族群詞彙的跨層一致性由 tests/contract/test_group_vocabulary.py 強制

export * from '../src/types';
