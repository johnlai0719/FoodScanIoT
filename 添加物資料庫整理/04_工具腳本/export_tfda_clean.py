"""
從主資料庫匯出純 TFDA 乾淨版本

只保留來自台灣食藥署的欄位，其餘一律留空（等待後續補充）。

輸出：01_主資料庫/additives_tfda_clean.json

欄位說明：
  TFDA 來源（已填）：record_id, name_zh, name_en, function_class,
                    regulatory_status_tw, usage_notes
  待補充（留空）    ：ins_or_e_number, synonyms, consumer_description,
                    adi_status, group_risks

用法：
  python 04_工具腳本/export_tfda_clean.py
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT        = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
MASTER_PATH = os.path.join(ROOT, '01_主資料庫', 'additives_master_candidate.json')
OUT_PATH    = os.path.join(ROOT, '01_主資料庫', 'additives_tfda_clean.json')


def export(master_data):
    result = []
    for r in master_data:
        record = {
            # ── TFDA 官方來源（已填）────────────────────────────
            "record_id":           r["record_id"],
            "name_zh":             r.get("additive_name_zh", ""),
            "name_en":             r.get("additive_name_en", ""),
            "function_class":      r.get("function_class", []),
            "regulatory_status_tw": r.get("regulatory_status_tw", ""),
            "usage_notes":         r.get("exception_notes", ""),

            # ── 待補充（留空）────────────────────────────────────
            "ins_or_e_number":     None,   # 來源：JECFA 官方（待 jecfa_fetch.py）
            "synonyms":            [],     # 來源：待補
            "consumer_description": None,  # 來源：Gemini（待 gemini_enrich.py）
            "adi_status":          "unknown",  # 來源：JECFA（待 jecfa_fetch.py）
            "group_risks":         [],     # 來源：人工審核白名單（待套用）
        }
        result.append(record)
    return result


def main():
    with open(MASTER_PATH, encoding='utf-8') as f:
        master_data = json.load(f)

    cleaned = export(master_data)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    print(f'匯出完成：{len(cleaned)} 筆')
    print(f'輸出：{OUT_PATH}')
    print()
    print('欄位狀態：')
    first = cleaned[0]
    for k, v in first.items():
        if v is None:
            status = '[待補] null'
        elif v == []:
            status = '[待補] []'
        elif v == 'unknown':
            status = '[待補] unknown'
        else:
            status = f'[TFDA] {str(v)[:40]}'
        print(f'  {k}: {status}')


if __name__ == '__main__':
    main()
