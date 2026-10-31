"""Offline additive matching agreement for fresh Vision A/B predictions.

The reference is GT ingredient text passed through the SAME matcher, not
independent additive annotations. Unmatched GT ingredients remain unverified.
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
# Import the competition matcher before the legacy helper can prepend another checkout.
sys.path.insert(0, str(ROOT/'server'))
from module_a import ingredient_matching as matcher
import evaluate_gemini_additives as helper

helper.SOURCE = ROOT
helper.m = matcher
assert Path(matcher.__file__).resolve() == ROOT/'server/module_a/ingredient_matching.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    manifest = helper.read(args.run/'manifest.json')
    inventory = {row['case_id']: row for row in manifest['inventory']}
    rows = [helper.read(p) for p in (args.run/'cases').glob('*.json')]
    paired = sorted([r for r in rows if all(r.get(m, {}).get('status') == 'completed' for m in ('A', 'B'))], key=lambda r:r['case_id'])
    knowledge, seed = helper.load_knowledge()
    details, review = [], []
    for r in paired:
        cid = r['case_id']; path = Path(inventory[cid]['gt'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == inventory[cid]['gt_sha256']
        target, unmatched = helper.extract(helper.read(path), knowledge)
        review.extend({'case_id':cid, 'gt_unmatched_item':name} for name in unmatched)
        row = {'case_id':cid, 'reference_additives':target}
        for mode in ('A', 'B'):
            found, unused = helper.extract(r[mode]['prediction'], knowledge)
            actual, predicted = set(target), set(found)
            row[mode] = {'tp':len(actual & predicted), 'fp':len(predicted-actual), 'fn':len(actual-predicted),
                         'additives':found, 'missing':sorted(actual-predicted), 'extra':sorted(predicted-actual)}
        details.append(row)
    result = {'cases':len(paired), 'knowledge_rows':len(knowledge),
              'metric':'seed-scoped additive matching agreement, micro, unique record_id per case',
              'reference':'GT ingredients processed through identical matcher; not independently annotated additive accuracy. Unmatched GT excluded, not confirmed non-additives.',
              'seed_sha256':hashlib.sha256(seed.read_bytes()).hexdigest(),
              'matcher_path':str(Path(matcher.__file__).resolve()),
              'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  (Path(matcher.__file__), ROOT/'server/module_a/ingredient_parser.py', ROOT/'tools/evaluate_gemini_additives.py', ROOT/'tools/evaluate_vision_additives.py')},
              'vector_rag':False, 'gt_unmatched_items':len(review), 'methods':{}}
    for mode in ('A', 'B'):
        counts = {k:sum(r[mode][k] for r in details) for k in ('tp','fp','fn')}
        tp,fp,fn=(counts[k] for k in ('tp','fp','fn'))
        counts.update(precision=tp/(tp+fp) if tp+fp else None, recall=tp/(tp+fn) if tp+fn else None,
                      f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)
        result['methods'][mode]=counts
    rng=np.random.default_rng(20261029)
    indices=rng.integers(0,len(details),size=(5000,len(details)))
    boot={}
    for mode in ('A','B'):
        sums=np.array([[r[mode][k] for k in ('tp','fp','fn')] for r in details])[indices].sum(axis=1)
        denom=2*sums[:,0]+sums[:,1]+sums[:,2]
        boot[mode]=np.divide(2*sums[:,0],denom,out=np.zeros(5000),where=denom!=0)
    result['paired_bootstrap_f1_difference_pp_95ci']=list(np.quantile((boot['B']-boot['A'])*100,[.025,.975]))
    out=args.run/'additives';out.mkdir(exist_ok=True)
    for filename,data in [('summary.json',result),('per_case.json',details)]:
        (out/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    with (out/'gt_unmatched_review.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['case_id','gt_unmatched_item']);writer.writeheader();writer.writerows(review)
    a,b=result['methods']['A'],result['methods']['B'];ci=result['paired_bootstrap_f1_difference_pp_95ci']
    text=f'''# 添加物比對：現行 Vision A/B 新輸出離線重評

日期：2026-10-29。使用同批共同成功{len(paired)}案，兩組均使用比賽分支的相同比對器與{len(knowledge)}項seed。沒有重新呼叫API或寫入資料庫。

| 指標 | A 整圖 Gemini | B Vision＋文字區塊＋Gemini |
|---|---:|---:|
| 命中參考添加物 TP | {a['tp']} | {b['tp']} |
| 參考外匹配 FP | {a['fp']} | {b['fp']} |
| 漏掉參考添加物 FN | {a['fn']} | {b['fn']} |
| Precision | {a['precision']:.2%} | {b['precision']:.2%} |
| Recall | {a['recall']:.2%} | {b['recall']:.2%} |
| Micro F1 | {a['f1']:.2%} | {b['f1']:.2%} |

B−A的F1差異為{(b['f1']-a['f1'])*100:+.2f}個百分點；配對案例bootstrap 5000次、seed 20261029，95%區間為[{ci[0]:.2f}, {ci[1]:.2f}]個百分點。此區間只反映案例抽樣，不是重複API呼叫的變異。

重要邊界：這是「GT成分原文經相同比對器所產生的添加物集合」與「模型成分原文經相同比對器所得集合」的一致性。GT沒有獨立逐項添加物標註，比對器的共同錯誤可能被隱藏，不能稱為獨立添加物辨識正確率或安全判定正確率。未匹配的GT項目共{len(review)}筆，另存CSV待查，未被當成已確認的非添加物。

各案優先解析ingredients_raw，必要時退回ingredients_list；每案以唯一資料庫record_id計數。僅執行確定性的名稱匹配，無Vector RAG、風險評分或用量／合法性判定。未配對成功的14案沒有納入此表。主實驗延遲與營養結果維持原報告。

比對器來源：`{matcher.__file__}`。本輪核對發現APP-sync-server主線比對器與比賽分支不同，因此明確使用比賽分支版本；與主線結果不可混用。
'''
    (out/'添加物比較.md').write_text(text,encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
