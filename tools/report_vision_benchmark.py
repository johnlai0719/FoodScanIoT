"""Offline report for a frozen benchmark run; never calls inference APIs."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from benchmark_vision_pipeline import summarize, timing


def report(output):
    manifest = json.loads((output/'manifest.json').read_text(encoding='utf-8'))
    changed = []
    for row in manifest['inventory']:
        for image in row['images']:
            if hashlib.sha256(Path(image['path']).read_bytes()).hexdigest() != image['sha256']:
                changed.append(image['path'])
        if hashlib.sha256(Path(row['gt']).read_bytes()).hexdigest() != row['gt_sha256']:
            changed.append(row['gt'])
    if changed:
        raise ValueError('Input/GT drift detected; inspect before reporting: '+str(changed))
    summary = summarize(output)
    records = [json.loads(p.read_text(encoding='utf-8')) for p in (output/'cases').glob('*.json')]
    paired = sorted([r for r in records if all(r.get(m, {}).get('status') == 'completed' for m in ('A', 'B'))], key=lambda r: r['case_id'])
    if not paired:
        raise ValueError('No paired completed cases')
    rng = np.random.default_rng(20261029)
    index = rng.integers(0, len(paired), size=(5000, len(paired)))
    boot = {}
    for mode in ('A', 'B'):
        values = np.array([[r[mode]['score'][k] for k in ('ingredient_exact_hit', 'ingredient_predicted', 'ingredient_gt')] for r in paired])
        sums = values[index].sum(axis=1)
        boot[mode] = np.divide(2*sums[:, 0], sums[:, 1]+sums[:, 2], out=np.zeros(5000), where=(sums[:, 1]+sums[:, 2]) != 0)
    latency = np.array([r['B']['reader_wall_s']-r['A']['reader_wall_s'] for r in paired])
    ci = {'method': 'Paired case bootstrap, 5000 samples, seed 20261029; case sampling uncertainty only, not repeated-call API variability',
          'ingredient_f1_B_minus_A_pp_95ci': list(np.quantile((boot['B']-boot['A'])*100, [.025, .975])),
          'reader_mean_B_minus_A_seconds_95ci': list(np.quantile(latency[index].mean(axis=1), [.025, .975]))}
    (output/'paired_uncertainty.json').write_text(json.dumps(ci, indent=2), encoding='utf-8')
    with (output/'per_case.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['case_id','A_reader_s','B_reader_s','B_vision_s','B_routing_s','A_gemini_s','B_gemini_s','A_ingredient_hit','B_ingredient_hit','A_ingredient_predicted','B_ingredient_predicted','ingredient_gt','A_numeric_correct','B_numeric_correct','A_tokens','B_tokens'])
        for r in paired:
            a,b=r['A'],r['B']; sa,sb=a['score'],b['score']
            writer.writerow([r['case_id'],a['reader_wall_s'],b['reader_wall_s'],b['stage_secs']['vision'],b['stage_secs']['routing'],a['call']['elapsed_s'],b['call']['elapsed_s'],sa['ingredient_exact_hit'],sb['ingredient_exact_hit'],sa['ingredient_predicted'],sb['ingredient_predicted'],sa['ingredient_gt'],sa['numeric_correct'],sb['numeric_correct'],a['call']['usage'].get('total_token_count'),b['call']['usage'].get('total_token_count')])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = 'Microsoft JhengHei'
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout='constrained')
    colors=['#718096','#3b866a']; labels=['A 整圖 Gemini','B Vision＋文字區塊＋Gemini']
    a,b=summary['A'],summary['B']
    outcomes = {mode: {status: sum(r.get(mode, {}).get('status') == status for r in records)
                      for status in ('completed', 'failed', 'running')} for mode in ('A', 'B')}
    for mode in ('A','B'):
        outcomes[mode]['all_finished_attempt_wall'] = timing([
            r[mode].get('reader_wall_s',r[mode].get('elapsed_s')) for r in records
            if r.get(mode,{}).get('status') in ('completed','failed')])
    (output/'outcomes.json').write_text(json.dumps(outcomes, indent=2), encoding='utf-8')
    failures=[{'case_id':r['case_id'],'mode':m,'elapsed_s':r[m].get('elapsed_s'),**r[m].get('error',{})}
              for r in records for m in ('A','B') if r.get(m,{}).get('status')=='failed']
    (output/'failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2),encoding='utf-8')
    quality=[a['scores']['ingredient_micro_f1']*100,b['scores']['ingredient_micro_f1']*100]
    bars=axes[0].bar(labels,quality,color=colors,width=.55)
    axes[0].set(ylim=(0,100),ylabel='成分完全相符 micro F1（%）',title='同批案例的抽取表現')
    axes[0].bar_label(bars,fmt='%.2f%%',padding=5)
    elapsed=[a['reader_wall']['mean_s'],b['reader_wall']['mean_s']]
    bars=axes[1].bar(labels,elapsed,color=colors,width=.55)
    axes[1].set(ylim=(0,max(elapsed)*1.25),ylabel='平均 reader 耗時（秒）',title='包含 Vision 與分區的辨識總耗時')
    axes[1].bar_label(bars,fmt='%.2fs',padding=5)
    for ax in axes:
        ax.spines[['top','right']].set_visible(False)
        ax.set_axisbelow(True);ax.grid(axis='y',alpha=.2)
    fig.suptitle(f'目前流程配對實測｜共同成功 {len(paired)} / {len(manifest["inventory"])} 案',fontsize=15)
    fig.savefig(output/'comparison.png',dpi=180);plt.close(fig)
    f1diff=(b['scores']['ingredient_micro_f1']-a['scores']['ingredient_micro_f1'])*100
    wall=b['reader_wall']['mean_s']-a['reader_wall']['mean_s']
    lines=['# Google Vision 現行流程：重新呼叫配對比較','',
           f'日期：2026-10-29；模型 `{manifest["model"]}`；每組每案各一次新呼叫；並行工作數 {manifest["workers"]}。',
           f'原始 {len(manifest["inventory"])} 案，兩組共同完成 {len(paired)} 案。結果為歷史開發資料探索性評估，非獨立驗收集。','',
           f'A完成 {outcomes["A"]["completed"]} 案、失敗 {outcomes["A"]["failed"]} 案；B完成 {outcomes["B"]["completed"]} 案、失敗 {outcomes["B"]["failed"]} 案。下表只使用兩組共同完成案例。','',
           '| 指標 | A 整圖 Gemini | B Vision＋通用文字區塊＋原圖裁圖 Gemini |','|---|---:|---:|']
    for label,av,bv in [
        ('成分完全相符 micro F1',f"{a['scores']['ingredient_micro_f1']:.2%}",f"{b['scores']['ingredient_micro_f1']:.2%}"),
        ('成分 Precision',f"{a['scores']['ingredient_precision']:.2%}",f"{b['scores']['ingredient_precision']:.2%}"),
        ('成分 Recall',f"{a['scores']['ingredient_recall']:.2%}",f"{b['scores']['ingredient_recall']:.2%}"),
        ('營養數值完全相符率',f"{a['scores']['nutrition_exact_accuracy']:.2%}",f"{b['scores']['nutrition_exact_accuracy']:.2%}"),
        ('配對成功 reader 平均耗時',f"{a['reader_wall']['mean_s']:.3f}s",f"{b['reader_wall']['mean_s']:.3f}s"),
        ('配對成功 reader p50',f"{a['reader_wall']['p50_s']:.3f}s",f"{b['reader_wall']['p50_s']:.3f}s"),
        ('配對成功 reader p95',f"{a['reader_wall']['p95_s']:.3f}s",f"{b['reader_wall']['p95_s']:.3f}s"),
        ('所有已結束嘗試平均（含失敗）',f"{outcomes['A']['all_finished_attempt_wall']['mean_s']:.3f}s",f"{outcomes['B']['all_finished_attempt_wall']['mean_s']:.3f}s"),
        ('所有已結束嘗試 p95（含失敗）',f"{outcomes['A']['all_finished_attempt_wall']['p95_s']:.3f}s",f"{outcomes['B']['all_finished_attempt_wall']['p95_s']:.3f}s"),
        ('Gemini 平均耗時',f"{a['gemini']['mean_s']:.3f}s",f"{b['gemini']['mean_s']:.3f}s"),
        ('Vision 平均耗時','—',f"{b['vision']['mean_s']:.3f}s"),
        ('分區平均耗時','—',f"{b['routing']['mean_s']:.3f}s"),
        ('Gemini total tokens（共同成功案例）',str(a['total_tokens']),str(b['total_tokens']))]:
        lines.append(f'| {label} | {av} | {bv} |')
    lines += ['',f'B−A：成分F1 {f1diff:+.2f} 個百分點，平均 reader 耗時 {wall:+.3f} 秒。',
              f'配對案例 bootstrap 95% 區間：F1差異 [{ci["ingredient_f1_B_minus_A_pp_95ci"][0]:.2f}, {ci["ingredient_f1_B_minus_A_pp_95ci"][1]:.2f}] 個百分點；平均耗時差異 [{ci["reader_mean_B_minus_A_seconds_95ci"][0]:.3f}, {ci["reader_mean_B_minus_A_seconds_95ci"][1]:.3f}] 秒。只反映案例抽樣，未量測同一圖片重複 API 呼叫的變異。','',
              '## 評分與邊界','',
              '- 成分是字串 multiset 完全相符 micro F1；不是模糊比對或添加物比對F1。營養為每份／每100共18欄的數值完全相等；錯值、缺值、硬填分開記錄於 summary.json。',
              '- A/B使用相同來源照片，保留的原圖均縮至長邊2048。B額外執行現行Vision、密集區域合併及多裁圖；維持production的裁圖上下文提示詞尾句。比較的是整套流程，不單獨歸因OCR或裁切。',
              '- B直接執行現行reader程式，僅在評測行程停用儀表板emit與服務鎖等待；不修改正在執行的服務。A/B順序依案例交替，無自動重試。',
              '- reader耗時包含圖片解碼、前處理、B認證／Vision、分區及Gemini；不包含App/Fog傳輸、快取、資料庫計分及觀測事件。推論不接觸GT，完成後才讀GT評分。',
              '- B的測試攔截器會保存Vision回應，這段額外寫檔時間包含在Vision與reader耗時內；未單獨扣除。tokens表只加總共同成功案例，不含未完成／未配對的已計費請求，不能當作整批帳單。',
              '- 來源圖、GT、程式與提示詞均有manifest雜湊；原始Vision回應、Gemini輸出與usage逐案保存。本次有重新計費的Vision與Gemini呼叫，tokens不是帳單金額。',
              '- 單次配對量測，不足以代表所有包裝與網路環境。若有失敗，主表只列共同完成者，需連同失敗率一起解讀。','',
              '## 未同時成功的案例','',json.dumps(summary['failed_or_partial'],ensure_ascii=False),'',
              '![配對比較](comparison.png)']
    (output/'結果報告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    differences=[]
    for r in paired:
        gtrow=next(row for row in manifest['inventory'] if row['case_id']==r['case_id'])
        gt=json.loads(Path(gtrow['gt']).read_text(encoding='utf-8'))
        cells=[]
        for group in ('nutrition','nutrition_per_serving'):
            for field, target in (gt.get(group) or {}).items():
                va=r['A']['prediction'][group].get(field);vb=r['B']['prediction'][group].get(field)
                if target is not None and va != vb:
                    cells.append({'group':group,'field':field,'GT':target,'A':va,'B':vb})
        differences.append({'case_id':r['case_id'],
                            'numeric_correct_B_minus_A':r['B']['score']['numeric_correct']-r['A']['score']['numeric_correct'],
                            'ingredient_hit_B_minus_A':r['B']['score']['ingredient_exact_hit']-r['A']['score']['ingredient_exact_hit'],
                            'input_images_A':r['A']['input_count'],'input_images_B':r['B']['input_count'],
                            'different_nutrition_cells':cells})
    (output/'case_differences.json').write_text(json.dumps(differences,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    print(json.dumps(report(parser.parse_args().output),ensure_ascii=True))
