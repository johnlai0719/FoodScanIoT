"""
清除 pubchem_patch.json 中確認比對錯誤的 CID 記錄
將這些記錄標記為 _error，讓它們改由 handle_missing.py 處理

執行：
  python fix_wrong_cids.py
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT       = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PATCH_PATH = os.path.join(ROOT, '03_enrichment補充', 'pubchem_patch.json')
MASTER_PATH = os.path.join(ROOT, '01_主資料庫', 'additives_master_candidate.json')

# ── 確認比對錯誤的清單 ────────────────────────────────────────────────
# 判斷依據：分子式與化合物類型明顯不符
WRONG_CID_RECORDS = {
    # 明礬類（無機鹽）比對到有機物或完全不同的無機物
    'ADD-0068': '燒鉀明礬 → CID 匹配到 K3Li2Nb5O15（鈮酸鹽），應為 KAl(SO4)2',
    'ADD-0070': '燒銨明礬 → CID 匹配到 C4H12N+（有機胺），應為 (NH4)Al(SO4)2',
    'ADD-0078': '酸式磷酸鋁鈉 → CID 匹配到 C31H48Na2O8P2（大分子有機），應為無機鋁磷酸鹽',
    'ADD-0079': '燒鈉明礬 → CID 匹配到 C16H29N3NaO14S2+（有機磺酸），應為 NaAl(SO4)2',

    # 醋酸鈉：應為 C2H3NaO2 (MW=82)，但匹配到 C4H5NaO3 (MW=124)
    'ADD-0116': '醋酸鈉 → CID 匹配到 C4H5NaO3（非醋酸鈉）',

    # 著色劑鋁麗基：分子式明顯錯誤
    'ADD-0497': '食用紅色七號鋁麗基 → formula C60H24Al2I12O15（含12個碘，不合理）',
    'ADD-0505': '食用藍色一號鋁麗基 → formula C37H34N2Na2O9S3（含Na無Al，是染料本身非鋁麗基）',
    'ADD-0507': '食用藍色二號鋁麗基 → formula 與黃色四號相同（疑似比對到錯誤化合物）',
    'ADD-0523': '喹啉黃鋁麗基 → formula C18H11NO2（無Al無S，非鋁麗基）',
    'ADD-0524': '食用紅色六號鋁麗基 → formula C36H75AlO12S3（脂肪磺酸鹽結構，非食用色素）',

    # 玉米糖膠：多醣，匹配到 La+3（鑭離子）
    'ADD-0675': '玉米糖膠 → CID 匹配到 La+3（鑭離子），應為多醣聚合物',

    # 修飾澱粉類：應為高分子，但匹配到小分子片段
    'ADD-0686': '羥丙基磷酸二澱粉 → formula C3H8O4P-（太小，匹配到磷酸片段而非澱粉）',
    'ADD-0694': '磷酸二澱粉 → formula C4H11O2PS2（太小，匹配到硫代磷酸鹽）',
    'ADD-0695': '磷酸化磷酸二澱粉 → formula C5H13O14P3（太小）',
    'ADD-0696': '乙醯化磷酸二澱粉 → formula C2H5O5P（太小，僅磷酸乙酯）',

    # 磷酸甘油酯：應含 P，但 formula C23H46O4 完全無磷
    'ADD-0744': '磷酸甘油酯 → formula C23H46O4（無磷元素，比對錯誤）',

    # 液態石蠟：烴類不應含氧
    'ADD-0770': '液態石蠟（礦物油）→ formula C15H16O6（含氧，比對到其他化合物）',
}


def main():
    with open(MASTER_PATH, encoding='utf-8') as f:
        master = {r['record_id']: r for r in json.load(f)}

    with open(PATCH_PATH, encoding='utf-8') as f:
        patch_list = json.load(f)

    patch_dict = {p['record_id']: p for p in patch_list}

    fixed = 0
    already_error = 0
    not_found = 0

    for rid, reason in WRONG_CID_RECORDS.items():
        if rid not in patch_dict:
            print(f'⚠️  {rid} 不在 patch 中')
            not_found += 1
            continue

        p = patch_dict[rid]
        if '_error' in p:
            print(f'ℹ️  {rid} 已是 _error 狀態')
            already_error += 1
            continue

        m = master.get(rid, {})
        zh = m.get('additive_name_zh', '')
        en = m.get('additive_name_en', '')

        # 清除錯誤的 PubChem 欄位，保留識別資訊
        patch_dict[rid] = {
            'record_id':      rid,
            'name_zh':        zh,
            'name_en':        en,
            '_error':         f'wrong_cid_match',
            '_skip_reason':   f'CID 比對錯誤：{reason}',
        }
        print(f'✅ {rid} {zh} → 已清除錯誤 CID，改為 missing')
        fixed += 1

    with open(PATCH_PATH, 'w', encoding='utf-8') as f:
        json.dump(list(patch_dict.values()), f, ensure_ascii=False, indent=2)

    print(f'\n共清除 {fixed} 筆錯誤 CID 記錄')
    print(f'已是 _error：{already_error} 筆')
    print(f'最新統計：')

    updated = list(patch_dict.values())
    n_ok      = sum(1 for p in updated if '_error' not in p and p.get('pubchem_raw_file'))
    n_error   = sum(1 for p in updated if '_error' in p)
    n_adi     = sum(1 for p in updated if p.get('adi_value'))
    n_ghs     = sum(1 for p in updated if p.get('ghs_hazard_codes'))
    print(f'   有 PubChem 原文：{n_ok} 筆')
    print(f'   查無/比對錯誤：{n_error} 筆')
    print(f'   有 ADI：{n_adi} 筆')
    print(f'   有 GHS：{n_ghs} 筆')
    print(f'\n下一步：執行 handle_missing.py 處理這 {n_error} 筆')


if __name__ == '__main__':
    main()
