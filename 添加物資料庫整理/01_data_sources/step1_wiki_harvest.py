#!/usr/bin/env python3
# 從 wiki_cache 的 matched_title 逐筆抓維基外部連結，篩出權威一手來源，產出對照表。
# 免費 MediaWiki API、不碰 DB。可重跑（會略過已完成的 record）。
import json, csv, glob, os, time, urllib.request, urllib.parse, sys

CACHE_DIR = os.path.join(os.path.dirname(__file__), 'wiki_cache')
OUT_CSV   = os.path.join(os.path.dirname(__file__), 'harvested_links', 'wiki_source_map.csv')
LOG       = OUT_CSV + '.log'

# 權威網域 → 欄位歸類
BUCKETS = {
    'jecfa_inchem': ('inchem.org', 'jecfa'),
    'efsa':         ('efsa.europa.eu',),
    'who_fao':      ('who.int', 'fao.org', 'codexalimentarius'),
    'fda':          ('fda.gov',),
    'pubmed':       ('pubmed.ncbi.nlm.nih.gov',),
    'pubchem':      ('pubchem.ncbi.nlm.nih.gov',),
    'echa':         ('echa.europa.eu',),
}
FIELDS = ['record_id','name_zh','name_en','matched_title',
          'jecfa_inchem','efsa','who_fao','fda','pubmed','pubchem','echa',
          'total_extlinks','auth_count']

def _get(url):
    """帶退讓重試的 GET：遇 429/503 指數退讓，尊重 Retry-After。"""
    delay=2.0
    for attempt in range(6):
        try:
            req=urllib.request.Request(url, headers={'User-Agent':'FoodScanIoT-research/1.0 (academic; contact via repo)'})
            return json.load(urllib.request.urlopen(req, timeout=25))
        except urllib.error.HTTPError as e:
            if e.code in (429,503) and attempt<5:
                wait=float(e.headers.get('Retry-After') or delay)
                time.sleep(min(wait,30)); delay*=2; continue
            raise
    raise RuntimeError('retry exhausted')

def extlinks(title):
    base='https://en.wikipedia.org/w/api.php'
    out=[]; cont={}
    while True:
        params={'action':'query','prop':'extlinks','titles':title,
                'ellimit':'500','format':'json','maxlag':'5'}; params.update(cont)
        url=base+'?'+urllib.parse.urlencode(params)
        data=_get(url)
        for p in data.get('query',{}).get('pages',{}).values():
            for el in p.get('extlinks',[]):
                out.append(el.get('*') or el.get('url') or list(el.values())[0])
        if 'continue' in data: cont=data['continue']
        else: break
    return out

def bucketize(links):
    res={k:[] for k in BUCKETS}
    for l in links:
        ll=l.lower()
        for field,doms in BUCKETS.items():
            if any(d in ll for d in doms):
                res[field].append(l); break
    return res

def log(msg):
    with open(LOG,'a') as f: f.write(msg+'\n')
    print(msg, flush=True)

def main():
    files=sorted(glob.glob(os.path.join(CACHE_DIR,'*.json')))
    done=set()
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV) as f:
            for r in csv.DictReader(f): done.add(r['record_id'])
    write_header = not os.path.exists(OUT_CSV)
    out=open(OUT_CSV,'a',newline='')
    w=csv.DictWriter(out,fieldnames=FIELDS)
    if write_header: w.writeheader()

    total=len(files); processed=0; skipped_nf=0; errs=0
    log(f"[start] {total} 筆, 已完成 {len(done)} 筆")
    for i,fn in enumerate(files,1):
        d=json.load(open(fn))
        rid=d['record_id']
        if rid in done: continue
        if d.get('status')!='found':
            skipped_nf+=1
            w.writerow({'record_id':rid,'name_zh':d.get('name_zh',''),
                        'name_en':d.get('name_en',''),'matched_title':'',
                        'total_extlinks':0,'auth_count':0,
                        **{k:'' for k in BUCKETS}})
            out.flush(); continue
        title=d['matched_title']
        try:
            links=extlinks(title)
            b=bucketize(links)
            row={'record_id':rid,'name_zh':d.get('name_zh',''),
                 'name_en':d.get('name_en',''),'matched_title':title,
                 'total_extlinks':len(links),
                 'auth_count':sum(len(v) for v in b.values())}
            for k in BUCKETS: row[k]=' | '.join(b[k])
            w.writerow(row); out.flush(); processed+=1
        except Exception as e:
            errs+=1; log(f"[err] {rid} {title}: {e}")
        if i%50==0: log(f"  ...{i}/{total} (new={processed}, nf={skipped_nf}, err={errs})")
        time.sleep(0.7)
    log(f"[done] new={processed}, not_found_skipped={skipped_nf}, errors={errs}, csv={OUT_CSV}")

if __name__=='__main__':
    main()
