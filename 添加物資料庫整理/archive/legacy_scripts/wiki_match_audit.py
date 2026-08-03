#!/usr/bin/env python3
# 稽核 wiki_cache 的配對品質，標記每筆可信度，輸出 wiki_match_audit.csv。
# 純讀檔分類，不碰 DB。
import json, glob, csv, os, re, collections

CACHE = os.path.join(os.path.dirname(__file__), '..', '03_enrichment補充', 'wiki_cache')
OUT   = os.path.join(os.path.dirname(__file__), '..', '03_enrichment補充',
                     'adi_run_20260618', 'wiki_match_audit.csv')

METALS = ['鉻','鋅','鉬','鈣','銅','鎂','錳','鐵','鉀','鈉','鋁','硼','亞鐵','鎳','鈷','硒']

def tokens(s):
    s=(s or '').lower()
    s=re.sub(r'\([^)]*\)', ' ', s)              # 去括號內容
    s=re.sub(r'\b(i{1,3}|iv|v)\b', ' ', s)      # 去羅馬數字 (ii)(iii)
    s=re.sub(r'[^a-z0-9 ]', ' ', s)
    toks=set(t for t in s.split() if len(t)>1)
    return toks

def sim(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb: return 0.0
    return len(ta & tb) / len(ta | tb)

rows=[]
for fn in glob.glob(os.path.join(CACHE,'*.json')):
    rows.append(json.load(open(fn)))
rows.sort(key=lambda r:r['record_id'])

# 統計每個 matched_title 被幾筆共用
share=collections.Counter(r['matched_title'] for r in rows if r.get('status')=='found')

out=[]
flagcount=collections.Counter()
for r in rows:
    rid=r['record_id']; zh=r.get('name_zh',''); en=r.get('name_en','')
    mt=r.get('matched_title',''); st=r.get('status')
    if st!='found':
        flag='NO_WIKI'; note='無維基條目'; s=0; sc=0
    else:
        sc=share[mt]
        s=round(sim(en, mt), 2)
        is_metal_salt = any(m in zh for m in METALS)
        if sc>1 and is_metal_salt:
            flag='SALT_SHARED'; note=f'金屬鹽，與其他 {sc-1} 筆共用同一條目（疑配到母酸）'
        elif sc>1:
            flag='SHARED'; note=f'與其他 {sc-1} 筆共用同一條目（需確認是否同物）'
        elif s>=0.5:
            flag='OK'; note='名稱高度相符'
        elif s>=0.34:
            flag='OK_SYNONYM'; note='名稱部分相符（多為同義詞/正式命名）'
        else:
            flag='REVIEW'; note='名稱落差大，疑似配錯，建議人工確認'
    flagcount[flag]+=1
    out.append({'record_id':rid,'name_zh':zh,'name_en':en,'matched_title':mt,
                'status':st,'shared_count':sc,'name_sim':s,'flag':flag,'note':note})

with open(OUT,'w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['record_id','name_zh','name_en','matched_title',
                                   'status','shared_count','name_sim','flag','note'])
    w.writeheader(); w.writerows(out)

print(f"輸出: {OUT}\n總計 {len(out)} 筆\n=== flag 分布 ===")
order=['OK','OK_SYNONYM','SALT_SHARED','SHARED','REVIEW','NO_WIKI']
for k in order:
    print(f"  {k:12s}: {flagcount[k]}")
print("\n=== REVIEW（疑似配錯）全列 ===")
for r in out:
    if r['flag']=='REVIEW':
        print(f"  {r['record_id']} {r['name_zh']}: '{r['name_en']}' → '{r['matched_title']}'")
