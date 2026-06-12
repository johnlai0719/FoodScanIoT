import json, sys
sys.stdout.reconfigure(encoding='utf-8')

with open('all_slim_pubchem_patch.json', encoding='utf-8') as f:
    patch = json.load(f)

with open('../01_主資料庫/additives_master_candidate.json', encoding='utf-8') as f:
    master = {r['record_id']: r for r in json.load(f)}

errors = [p for p in patch if '_error' in p]
print(f"查無資料：{len(errors)} 筆\n")
for p in errors:
    rid = p.get('record_id', '')
    rec = master.get(rid, {})
    print(f"  {rid}  {rec.get('additive_name_zh','')} / {rec.get('additive_name_en','')}")
    print(f"    類別：{rec.get('function_class', [])}")
    reason = p.get('_skip_reason', '')
    if reason:
        tried = reason.replace('已嘗試變體：', '')
        print(f"    嘗試過：{tried[:80]}")
