#!/usr/bin/env python3
# 省 token 前處理:
#  1. 已在 results.csv 的(跑過的)略過。
#  2. 頁面完全沒提 ADI 的 → 直接寫 unknown 進 results.csv,不送 LLM。
#  3. 有 ADI 的 → 把 pages 內容裁成「標題 + ADI 附近段落」,大幅縮短,
#     並列入 batch_新增需LLM.txt 交給 AGY。
# 完整原文仍保留在 source_archive/ 與 tavily_raw/,故可重現。
import json, glob, os, re, csv

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, 'inputs')
RESULTS = os.path.join(HERE, 'outputs', 'results.csv')
MANIFEST = os.path.join(HERE, 'batch_新增需LLM.txt')

PAT = re.compile(r'acceptable daily intake|\bADI\b', re.I)
HEADER_KEEP = 800     # 保留每頁前段(通常含化學名,供身分確認)
WIN = 700             # ADI 命中前後各保留字元數

def done_ids():
    if not os.path.exists(RESULTS): return set()
    return {r['record_id'] for r in csv.DictReader(open(RESULTS))}

def trim(text):
    keep = [(0, min(HEADER_KEEP, len(text)))]
    for m in PAT.finditer(text):
        keep.append((max(0, m.start() - WIN), min(len(text), m.end() + WIN)))
    keep.sort()
    merged = []
    for s, e in keep:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return ' […] '.join(text[s:e] for s, e in merged)

def main():
    done = done_ids()
    skipped = no_adi = need_llm = 0
    manifest = []
    unknown_rows = []
    for fn in sorted(glob.glob(os.path.join(INPUTS, '*.json'))):
        d = json.load(open(fn))
        if not d.get('pages'):
            continue
        if d['record_id'] in done:
            skipped += 1
            continue
        full = ' '.join(p['content'] for p in d['pages'])
        if not PAT.search(full):
            no_adi += 1
            unknown_rows.append({'record_id': d['record_id'], 'adi': 'unknown',
                                 'source_url': '', 'quote': '', 'matched_name': ''})
            continue
        # 有 ADI:裁切每頁內容
        for p in d['pages']:
            p['content'] = trim(p['content'])
        json.dump(d, open(fn, 'w'), ensure_ascii=False, indent=1)
        manifest.append(d['record_id'])
        need_llm += 1

    # 沒 ADI 的直接補 unknown 進 results.csv
    if unknown_rows:
        exists = os.path.exists(RESULTS)
        with open(RESULTS, 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['record_id', 'adi', 'source_url', 'quote', 'matched_name'])
            if not exists: w.writeheader()
            w.writerows(unknown_rows)

    open(MANIFEST, 'w').write('\n'.join(manifest) + ('\n' if manifest else ''))
    print(f"已跑過(略過)       : {skipped}")
    print(f"沒提ADI→直接unknown : {no_adi}（已寫進 results.csv,未送LLM）")
    print(f"需送 AGY 的         : {need_llm}  -> {os.path.basename(MANIFEST)}")

if __name__ == '__main__':
    main()
