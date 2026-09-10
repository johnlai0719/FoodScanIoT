#!/usr/bin/env python3
# 掃 PP-OCR 的推論參數，找出讀得到最多營養表小字的設定。
#
# 為什麼值得掃：這些全是零成本的變因。已經吃過一次虧——把
# text_det_limit_side_len 從預設 960 改成 2048，就讓 c39_米香 從 0 框變 50 框。
# 在投入任何標註或訓練之前，先確定不是又輸在某個沒調的參數上。
#
# 目標指標是**營養數值漏讀數**：v6 開箱在 58 案上漏 70 個，而 Gemini 只漏 6。
# 那 66-70 個裡實測有 57 個是「文字裡完全沒出現」，屬偵測讀不到，
# 正是這些參數影響得到的範圍。
#
# 效率考量：limit_side_len / thresh / box_thresh / unclip_ratio 都是 predict()
# 的參數，換它們不必重載模型；只有 use_doc_unwarping 這種要重建 PaddleOCR。
# 所以外層迴圈是 init 設定，內層才是 predict 設定。
#
# 用法：
#   python sweep_params.py --preset=v6_hires
#   python sweep_params.py --preset=v6_hires --cases c02 c09 c38 c59   # 只跑問題案例
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_baseline as RB  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# (名稱, init 覆寫, predict 覆寫)
# 每組只動一個變因，這樣有效果時歸因得了。
VARIANTS = [
    ('base',            {}, {}),
    ('side3072',        {}, {'text_det_limit_side_len': 3072}),
    ('side4096',        {}, {'text_det_limit_side_len': 4096}),
    # limit_type='min' 是「短邊至少 N」，對長寬比極端的照片比 max 更能保住細節
    ('min1600',         {}, {'text_det_limit_side_len': 1600, 'text_det_limit_type': 'min'}),
    # box_thresh 調低 = 接受更弱的偵測。小字通常就是弱回應
    ('boxth0.4',        {}, {'text_det_box_thresh': 0.4}),
    ('boxth0.3',        {}, {'text_det_box_thresh': 0.3}),
    # thresh 是二值化門檻，調低讓筆畫淡的字更容易連成區塊
    ('th0.2',           {}, {'text_det_thresh': 0.2}),
    # unclip 放大框；框太緊會把字邊切掉，rec 就讀不全
    ('unclip2.5',       {}, {'text_det_unclip_ratio': 2.5}),
    ('side3072+boxth0.4', {}, {'text_det_limit_side_len': 3072, 'text_det_box_thresh': 0.4}),
    # UVDoc 曲面校正。泡麵杯那類理論上該有幫助，代價是每張多一次推論
    ('unwarp',          {'use_doc_unwarping': True}, {}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preset', default='v6_hires')
    ap.add_argument('--cases', nargs='*', default=None)
    ap.add_argument('--device', default='gpu')
    ap.add_argument('--only', nargs='*', default=None, help='只跑指定的變體名稱')
    a = ap.parse_args()

    base = RB.PRESETS[a.preset]
    cases = RB.load_cases(a.cases, 0)
    variants = [v for v in VARIANTS if not a.only or v[0] in a.only]
    print(f'preset={a.preset}｜案例 {len(cases)}｜變體 {len(variants)}\n')

    from paddleocr import PaddleOCR

    # 依 init 設定分組，同組共用一個模型實例
    groups = {}
    for name, ini, pre in variants:
        groups.setdefault(json.dumps(ini, sort_keys=True), []).append((name, pre))

    for ini_key, items in groups.items():
        ini = {**base['init'], **json.loads(ini_key)}
        t0 = time.time()
        ocr = PaddleOCR(device=a.device, **ini)
        print(f'載入模型 {time.time() - t0:.0f}s  init覆寫={json.loads(ini_key) or "(無)"}')

        for name, pre_over in items:
            pre = {**base['predict'], **pre_over}
            outdir = os.path.join(HERE, 'out', f'{a.preset}__{name}')
            os.makedirs(outdir, exist_ok=True)
            t1, nb = time.time(), 0
            for case in cases:
                rec = {'case_id': case['case_id'], 'preset': f'{a.preset}__{name}',
                       'set_version': case.get('set_version'),
                       'category': case.get('category'), 'images': []}
                for rel in case['images']:
                    p = os.path.join(RB.EVAL_ROOT, rel)
                    if not os.path.exists(p):
                        rec['images'].append({'path': rel, 'error': 'missing'})
                        continue
                    t2 = time.time()
                    try:
                        results = ocr.predict(p, **pre)
                    except Exception as e:
                        rec['images'].append({'path': rel, 'error': repr(e)})
                        continue
                    lines = []
                    for r in results:
                        lines.extend(RB.extract_lines(r))
                    nb += len(lines)
                    rec['images'].append({'path': rel, 'elapsed_s': round(time.time() - t2, 2),
                                          'n_lines': len(lines), 'lines': lines})
                with open(os.path.join(outdir, f'{case["case_id"]}.json'), 'w',
                          encoding='utf-8') as f:
                    json.dump(rec, f, ensure_ascii=False, indent=1)
            print(f'  {name:<20} {nb:>5} 框  {time.time() - t1:>5.0f}s  '
                  f'predict覆寫={pre_over or "(無)"}')

    print(f'\n評分：python sweep_params.py --score  或直接用 score_ocr.py --preset=…')


if __name__ == '__main__':
    main()
