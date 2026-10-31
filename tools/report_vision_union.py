"""Offline three-way comparison, with additive agreement as the primary metric."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from benchmark_vision_pipeline import timing, digest
from evaluate_vision_additives import helper, matcher


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def quality(records):
    totals={k:sum(r['score'][k] for r in records) for k in ('ingredient_exact_hit','ingredient_predicted','ingredient_gt',
                'numeric_correct','numeric_wrong','numeric_missing','numeric_hardfill')}
    h,p,g=[totals[k] for k in ('ingredient_exact_hit','ingredient_predicted','ingredient_gt')]
    n=sum(totals[k] for k in ('numeric_correct','numeric_wrong','numeric_missing'))
    return {**totals,'ingredient_f1':2*h/(p+g) if p+g else None,
            'nutrition_exact_accuracy':totals['numeric_correct']/n if n else None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    args=parser.parse_args();run=args.run
    manifest=read(run/'manifest.json');source=Path(manifest['source_run'])
    assert digest((source/'manifest.json').read_bytes())==manifest['source_manifest_sha256']
    root=Path(__file__).resolve().parents[1]
    assert all(digest((root/'tools'/name).read_bytes())==sha for name,sha in manifest['source_sha256'].items())
    assert all(digest(Path(p).read_bytes())==sha for p,sha in manifest['vision_hashes'].items())
    inventory={r['case_id']:r for r in manifest['inventory']}
    for row in inventory.values():
        assert digest(Path(row['gt']).read_bytes())==row['gt_sha256']
        assert all(digest(Path(im['path']).read_bytes())==im['sha256'] for im in row['images'])
    ab={p.stem:read(p) for p in (source/'cases').glob('*.json')}
    c={p.stem:read(p) for p in (run/'cases').glob('*.json')}
    assert len(ab)==len(c)==len(inventory)
    assert all(r.get('status') in ('completed','failed') for r in c.values())
    ids=sorted(k for k in ab.keys()&c.keys() if c[k]['status']=='completed' and all(ab[k].get(m,{}).get('status')=='completed' for m in ('A','B')))
    assert ids
    arms={m:[ab[k][m] if m!='C' else c[k] for k in ids] for m in ('A','B','C')}
    knowledge,seed=helper.load_knowledge();details=[];review=[]
    for cid in ids:
        target,unmatched=helper.extract(read(Path(inventory[cid]['gt'])),knowledge)
        review.extend({'case_id':cid,'gt_unmatched_item':v} for v in unmatched)
        record={'case_id':cid,'reference_additives':target}
        for mode in ('A','B','C'):
            prediction=ab[cid][mode]['prediction'] if mode!='C' else c[cid]['prediction']
            found,unused=helper.extract(prediction,knowledge)
            target_ids,predicted_ids=set(target),set(found)
            record[mode]={'tp':len(target_ids&predicted_ids),'fp':len(predicted_ids-target_ids),'fn':len(target_ids-predicted_ids),
                          'additives':found,'missing':sorted(target_ids-predicted_ids),'extra':sorted(predicted_ids-target_ids)}
        details.append(record)
    result={'cases_requested':len(inventory),'three_way_common_success':len(ids),'case_ids':ids,
            'knowledge_rows':len(knowledge),'seed_sha256':digest(seed.read_bytes()),
            'matcher_sha256':digest(Path(matcher.__file__).read_bytes()),
            'evaluation_source_sha256':{str(p.relative_to(root)):digest(p.read_bytes()) for p in
                (Path(matcher.__file__),Path(matcher.__file__).parent/'ingredient_parser.py',
                 root/'tools/evaluate_gemini_additives.py',root/'tools/evaluate_vision_additives.py',Path(__file__).resolve())},
            'additive_reference':'GT ingredient text processed through identical matcher; not independent additive annotations; unmatched GT excluded, not confirmed non-additives.',
            'arms':{},'success_counts':{m:sum((r.get(m,{}).get('status') if m!='C' else r.get('status'))=='completed' for r in (ab.values() if m!='C' else c.values())) for m in ('A','B','C')}}
    prior_additives=read(source/'additives/summary.json')
    assert result['seed_sha256']==prior_additives['seed_sha256']
    assert result['matcher_sha256']==prior_additives['source_sha256']['server\\module_a\\ingredient_matching.py']
    for mode,records in arms.items():
        totals={k:sum(r[mode][k] for r in details) for k in ('tp','fp','fn')}
        tp,fp,fn=[totals[k] for k in ('tp','fp','fn')]
        totals.update(precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None,
                      f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)
        row={'additives':totals,'quality':quality(records),'gemini':timing([r['call']['elapsed_s'] for r in records]),
             'total_tokens':sum(r['call']['usage'].get('total_token_count',0) or 0 for r in records)}
        if mode=='C':
            assert all(r['estimated_fresh_wall_s'] is not None for r in records)
            row.update(cached_reader=timing([r['cached_reader_wall_s'] for r in records]),
                       estimated_fresh_reader=timing([r['estimated_fresh_wall_s'] for r in records]),
                       crop=timing([r['crop_s'] for r in records]),
                       images=sum(len(r['regions']) for r in records),
                       mean_retained_area=float(np.mean([region['retained_area'] for r in records for region in r['regions']])),
                       median_retained_area=float(np.median([region['retained_area'] for r in records for region in r['regions']])),
                       fallback_images=sum(bool(region['fallback']) for r in records for region in r['regions']))
        else:
            row['measured_reader']=timing([r['reader_wall_s'] for r in records])
        result['arms'][mode]=row
    rng=np.random.default_rng(20261029);indices=rng.integers(0,len(ids),size=(5000,len(ids)));boot={}
    for mode in ('A','B','C'):
        sums=np.array([[r[mode][k] for k in ('tp','fp','fn')] for r in details])[indices].sum(axis=1)
        denom=2*sums[:,0]+sums[:,1]+sums[:,2]
        boot[mode]=np.divide(2*sums[:,0],denom,out=np.zeros(5000),where=denom!=0)
    result['additive_f1_difference_pp_95ci']={f'C-{m}':list(np.quantile((boot['C']-boot[m])*100,[.025,.975])) for m in ('A','B')}
    for filename,data in [('three_way_summary.json',result),('additive_per_case.json',details)]:
        (run/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    with (run/'gt_unmatched_review.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['case_id','gt_unmatched_item']);writer.writeheader();writer.writerows(review)
    with (run/'per_case.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['case_id','mode','tp','fp','fn','gemini_s','reader_s','reader_time_is_estimate','tokens'])
        for record in details:
            cid=record['case_id']
            for mode in ('A','B','C'):
                r=ab[cid][mode] if mode!='C' else c[cid]
                writer.writerow([cid,mode,record[mode]['tp'],record[mode]['fp'],record[mode]['fn'],r['call']['elapsed_s'],
                    r.get('reader_wall_s') if mode!='C' else r['estimated_fresh_wall_s'],mode=='C',r['call']['usage'].get('total_token_count')])
    a,b,cc=[result['arms'][m] for m in ('A','B','C')]
    lines=['# 單一文字大框：三組比較','',
           f'日期：2026-10-29。A/B沿用前次新呼叫，C本輪重新呼叫Gemini，三組模型均為`{manifest["model"]}`。Vision使用前次已存回應，未重新呼叫。',
           f'原始{len(inventory)}案，A成功{result["success_counts"]["A"]}案，B成功{result["success_counts"]["B"]}案，C成功{result["success_counts"]["C"]}案。下表三欄均限制在相同的{len(ids)}個共同成功案例；所以A/B可能與前次163案主表不同。','',
           '| 指標 | A 整圖 | B 原圖＋多裁圖 | C 單一大框、只送裁圖 |','|---|---:|---:|---:|']
    def add(label,values):lines.append('| '+label+' | '+' | '.join(values)+' |')
    for label,key in [('添加物Precision','precision'),('添加物Recall','recall'),('添加物匹配一致性F1','f1')]:
        add(label,[f"{result['arms'][m]['additives'][key]:.2%}" for m in ('A','B','C')])
    for label,key in [('參考命中TP','tp'),('參考外匹配FP','fp'),('參考漏掉FN','fn')]:
        add(label,[str(result['arms'][m]['additives'][key]) for m in ('A','B','C')])
    for label,key in [('成分完全相符F1','ingredient_f1'),('營養數值完全相符率','nutrition_exact_accuracy')]:
        add(label,[f"{result['arms'][m]['quality'][key]:.2%}" for m in ('A','B','C')])
    add('Gemini平均耗時',[f"{result['arms'][m]['gemini']['mean_s']:.3f}s" for m in ('A','B','C')])
    add('Gemini p95耗時',[f"{result['arms'][m]['gemini']['p95_s']:.3f}s" for m in ('A','B','C')])
    add('辨識流程平均耗時',[f"{a['measured_reader']['mean_s']:.3f}s",f"{b['measured_reader']['mean_s']:.3f}s",f"約{cc['estimated_fresh_reader']['mean_s']:.3f}s（分段相加估計）"])
    add('辨識流程p95耗時',[f"{a['measured_reader']['p95_s']:.3f}s",f"{b['measured_reader']['p95_s']:.3f}s",f"約{cc['estimated_fresh_reader']['p95_s']:.3f}s（分段相加估計）"])
    add('Gemini total tokens（共同成功）',[str(result['arms'][m]['total_tokens']) for m in ('A','B','C')])
    lines += ['',f"C實測、不含Vision的reader平均：{cc['cached_reader']['mean_s']:.3f}s。C的{cc['images']}張共同案例圖片中，{cc['fallback_images']}張依規則保留整圖；平均保留面積{cc['mean_retained_area']:.1%}、中位數{cc['median_retained_area']:.1%}。",'',
              '## 差異區間','']
    for m in ('A','B'):
        ci=result['additive_f1_difference_pp_95ci'][f'C-{m}'];diff=(cc['additives']['f1']-result['arms'][m]['additives']['f1'])*100
        lines.append(f'- C−{m}添加物F1：{diff:+.2f}pp；配對案例bootstrap 5000次、95%區間[{ci[0]:.2f}, {ci[1]:.2f}]pp。')
    lines += ['', '## 評估邊界','',
              '- 添加物指標是GT成分原文經相同比對器產生的參考集合與預測集合的一致性，沒有獨立添加物標註，可能隱藏共同規則錯誤；未匹配GT另存CSV，不當作已確認的非添加物。每案按唯一record_id計數，804項seed、比賽分支matcher，無Vector RAG／資料庫寫入／風險判定。',
              '- C每張照片將全部Vision字詞框合成一個外接框，保留4%／最少16px邊界，面積≥90%回原圖；只送裁圖，長邊2048。沒有營養分類、密集度篩選或額外原圖。多圖商品仍為一案。',
              '- A/C使用相同基本PROMPT與schema；B保留production多裁圖提示尾句。A/B/C都使用相同來源照片，原圖或裁圖輸入上限為2048。這是輸入策略比較，不能把差異全歸因OCR引擎。',
              '- C總耗時是本次cached-reader實測＋同案先前B的Vision段耗時，不是本輪實測端到端延遲。已把舊Vision認證／請求／保存回應時間算回去；不含App、Fog、DB與服務鎖等待。',
              '- A/B與C並非三組交錯同時重測，每組每案一次且不重試，API時間差與輸出變異仍可能影響結果。資料是歷史開發集，bootstrap只反映案例抽樣，不代表重複API變異。',
              '- 原始圖、GT、已存Vision與推論程式雜湊均已核對；本輪僅Gemini新呼叫，不改線上服務。','', '![三組比較](comparison.png)']
    (run/'三組比較.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    failures=[{'case_id':r['case_id'],'elapsed_s':r.get('elapsed_s'),**r.get('error',{})}
              for r in c.values() if r['status']=='failed']
    (run/'failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family']='Microsoft JhengHei';plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    labels=['A 整圖','B 原圖＋多裁圖','C 單一大框裁圖'];colors=['#718096','#ba8b38','#3b866a']
    values=[result['arms'][m]['additives']['f1']*100 for m in ('A','B','C')]
    bars=axes[0].bar(labels,values,color=colors);axes[0].bar_label(bars,fmt='%.2f%%',padding=5)
    axes[0].set(ylim=(0,100),ylabel='添加物匹配一致性 micro F1（%）',title='同一參考集的添加物比對')
    values=[a['measured_reader']['mean_s'],b['measured_reader']['mean_s'],cc['estimated_fresh_reader']['mean_s']]
    bars=axes[1].bar(labels,values,color=colors);axes[1].bar_label(bars,labels=[f'{v:.2f}s' if i<2 else f'約{v:.2f}s*' for i,v in enumerate(values)],padding=5)
    axes[1].set(ylim=(0,max(values)*1.25),ylabel='平均辨識耗時（秒）',title='* C為本次裁圖流程＋前次Vision耗時')
    for ax in axes:
        ax.spines[['top','right']].set_visible(False);ax.set_axisbelow(True);ax.grid(axis='y',alpha=.2)
    fig.suptitle(f'Vision 單框對照｜三組共同成功{len(ids)}／{len(inventory)}案',fontsize=15)
    fig.savefig(run/'comparison.png',dpi=180);plt.close(fig)
    print(json.dumps({k:v for k,v in result.items() if k!='case_ids'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
