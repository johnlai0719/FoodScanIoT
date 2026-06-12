import json, sys
sys.stdout.reconfigure(encoding='utf-8')

with open('all_slim_pubchem_patch.json', encoding='utf-8') as f:
    patch = json.load(f)

errors  = [p for p in patch if '_error' in p]
has_raw = [p for p in patch if p.get('pubchem_raw_file')]
has_adi = [p for p in patch if p.get('adi_value')]
has_ghs = [p for p in patch if p.get('ghs_hazard_codes')]

print(f"總記錄數：{len(patch)}")
print(f"成功（有原文）：{len(has_raw)}")
print(f"失敗（查無資料）：{len(errors)}")
print(f"有 ADI 數值：{len(has_adi)}")
print(f"有 GHS 危害碼：{len(has_ghs)}")
print()
print("--- 失敗記錄清單 ---")
for p in errors:
    rid = p.get('record_id', '')
    zh  = p.get('name_zh', '')
    en  = p.get('name_en', '')
    print(f"  {rid}  {zh} / {en}")
